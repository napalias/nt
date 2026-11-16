"""Open scan problems, and the e-mail that tells the principal about them.

A source has an open problem while its last finished run shows one (a later good run
closes it). `/api/scrapes/attention` lists them all; the hourly `notify_scrape_problems`
task e-mails the ones that need a person:

- needs_human: a captcha, bot check, login wall or robots.txt ban
- blocked: nothing saved and the site said no (403/429)
- failed: the crawl crashed, timed out or was killed
- empty_twice: the last two finished runs both saved nothing

`partial` (saved some, with errors) is listed as a warning but never e-mailed.

Mail goes to settings.NOTIFY_EMAIL, at most once per source per 24 h, and each problem run
at most once; with no recipient set nothing is claimed, so the problem is still sent once
one is. Decision (principal, 2026-11-15): e-mail plus an attention banner on /scans.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from apps.listings.blocks import BlockKind
from apps.listings.models import ScrapeRun
from apps.listings.sources import SOURCES, Source

logger = logging.getLogger(__name__)

THROTTLE = timedelta(hours=24)
FROM_EMAIL = "noreply@nt.local"


class ProblemKind(StrEnum):
    NEEDS_HUMAN = "needs_human"
    BLOCKED = "blocked"
    FAILED = "failed"
    EMPTY_TWICE = "empty_twice"
    PARTIAL = "partial"


NOTIFY_KINDS = frozenset(
    {ProblemKind.NEEDS_HUMAN, ProblemKind.BLOCKED, ProblemKind.FAILED, ProblemKind.EMPTY_TWICE}
)

# What a person does about each block kind. Spelled for the principal, not for code.
BLOCK_ACTIONS: dict[BlockKind, str] = {
    BlockKind.CAPTCHA: (
        "Solve the captcha in a browser (a human-assisted session), then run the scan again."
    ),
    BlockKind.CHALLENGE: (
        "The site shows a bot check (Cloudflare or similar): pass it once in a browser; if it "
        "keeps coming back, change the source's fetch mode in the registry."
    ),
    BlockKind.LOGIN: (
        "The site asks you to log in: check in a browser whether these pages need an account. "
        "We store no credentials, so park the source if they do."
    ),
    BlockKind.ROBOTS: (
        "robots.txt forbids the start URL: pause the source or point it at an allowed URL in "
        "the registry (apps/listings/sources.py)."
    ),
    BlockKind.FORBIDDEN: (
        "The site answers 403: open the start URL in a browser; if it works there, slow the "
        "source down or change its fetch mode."
    ),
    BlockKind.RATE_LIMITED: (
        "The site answers 429 (too many requests): raise the source's delay in the registry "
        "and wait a day."
    ),
}


@dataclass(frozen=True)
class Problem:
    source: Source
    kind: ProblemKind
    run: ScrapeRun  # the last finished run, which shows the problem

    @property
    def label(self) -> str:
        return self.source.label

    @property
    def notifies(self) -> bool:
        return self.kind in NOTIFY_KINDS

    @property
    def severity(self) -> str:
        return "error" if self.notifies else "warning"

    @property
    def what_to_do(self) -> str:
        if self.run.block_kind:
            return BLOCK_ACTIONS[BlockKind(self.run.block_kind)]
        if self.kind == ProblemKind.NEEDS_HUMAN:
            return "The spider is waiting for a person: open /scans and follow the run's log."
        if self.kind == ProblemKind.FAILED:
            reason = self.run.finish_reason or "no reason recorded"
            return (
                f"The crawl failed ({reason}): read the log tail on the run, fix the spider "
                "or its start URL, then run it again."
            )
        if self.kind == ProblemKind.EMPTY_TWICE:
            return (
                "Two runs in a row saved nothing: open the start URL in a browser and check "
                f"the spider's selectors (pnpm inspect {self.source.spider})."
            )
        if self.kind == ProblemKind.PARTIAL:
            return (
                "Some pages failed or many items were dropped: check the run's errors and "
                "drop reasons."
            )
        # BLOCKED always carries a block kind (summarize_stats); this is an old row.
        return "The site blocked the crawl: open the start URL in a browser to see why."


def _problem_of(latest: ScrapeRun, previous: ScrapeRun | None) -> ProblemKind | None:
    """The problem the last finished run shows (needs-human first), or None for a good run
    or a single empty one."""
    if latest.needs_human:
        return ProblemKind.NEEDS_HUMAN
    if latest.status == ScrapeRun.Status.BLOCKED:
        return ProblemKind.BLOCKED
    if latest.status == ScrapeRun.Status.FAILED:
        return ProblemKind.FAILED
    if latest.status == ScrapeRun.Status.EMPTY:
        empty_before = previous is not None and previous.status == ScrapeRun.Status.EMPTY
        return ProblemKind.EMPTY_TWICE if empty_before else None
    if latest.status == ScrapeRun.Status.PARTIAL:
        return ProblemKind.PARTIAL
    return None


def open_problems() -> list[Problem]:
    """One problem per registry source whose last finished run shows one, registry order.

    Running rows are skipped (the run before them still speaks); runs of spiders that are
    not in the registry are ignored."""
    problems = []
    for source in SOURCES:
        finished = list(
            ScrapeRun.objects.filter(spider=source.spider)
            .exclude(status=ScrapeRun.Status.RUNNING)
            .order_by("-started_at")[:2]
        )
        if not finished:
            continue
        latest, previous = finished[0], (finished[1] if len(finished) > 1 else None)
        kind = _problem_of(latest, previous)
        if kind is not None:
            problems.append(Problem(source=source, kind=kind, run=latest))
    return problems


def _recently_notified(spider: str, now: datetime) -> bool:
    return ScrapeRun.objects.filter(spider=spider, notified_at__gte=now - THROTTLE).exists()


def _body(problems: list[Problem]) -> str:
    lines = ["These scans need attention:", ""]
    for p in problems:
        run = p.run
        detail = run.status + (f", {run.block_kind}" if run.block_kind else "")
        lines += [
            f"{p.label} ({p.source.spider}): {p.kind.value.replace('_', ' ')} [{detail}]",
            f"  Last run: {timezone.localtime(run.started_at):%Y-%m-%d %H:%M} ({run.trigger})",
        ]
        if run.start_url:
            lines.append(f"  Start URL: {run.start_url}")
        lines += [f"  What to do: {p.what_to_do}", ""]
    lines.append("Run details and log tails: the /scans page. Each source is e-mailed at most")
    lines.append("once a day.")
    return "\n".join(lines)


def send_problem_notifications(now: datetime | None = None) -> dict:
    """E-mail the open problems that need a person and were not told yet; one e-mail.

    The problem rows are locked while the mail goes out and marked `notified_at` only if
    it was sent, so two checks never mail the same run and a failed send is retried by
    the next check. A send error propagates (the Celery task fails loudly)."""
    now = now or timezone.now()
    if not settings.NOTIFY_EMAIL:
        logger.info("NOTIFY_EMAIL is not set; skipping scan problem e-mails")
        return {"notified": [], "skipped": "NOTIFY_EMAIL is not set"}

    candidates = [p for p in open_problems() if p.notifies and p.run.notified_at is None]
    with transaction.atomic():
        locked = {
            run.pk: run
            for run in ScrapeRun.objects.select_for_update().filter(
                pk__in=[p.run.pk for p in candidates]
            )
        }
        due = [
            p
            for p in candidates
            if p.run.pk in locked
            and locked[p.run.pk].notified_at is None
            and not _recently_notified(p.source.spider, now)
        ]
        if not due:
            return {"notified": [], "skipped": ""}
        labels = ", ".join(p.label for p in due)
        send_mail(
            subject=f"[NT] Scans need attention: {labels}",
            message=_body(due),
            from_email=FROM_EMAIL,
            recipient_list=[settings.NOTIFY_EMAIL],
        )
        ScrapeRun.objects.filter(pk__in=[p.run.pk for p in due]).update(notified_at=now)
    spiders = [p.source.spider for p in due]
    logger.info("E-mailed scan problems for %s", ", ".join(spiders))
    return {"notified": spiders, "skipped": ""}
