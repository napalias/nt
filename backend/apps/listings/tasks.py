import logging
import re
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import requests
from celery import shared_task
from django.conf import settings
from django.db.models import F
from django.utils import timezone

from apps.classifier import judge
from apps.listings.blocks import TITLE_RE, classify_response
from apps.listings.models import Listing, ScrapeRun
from apps.listings.scrape_runs import RUN_ID_SETTING, clip_start_url
from apps.listings.scrub import scrub_text
from apps.listings.sources import (
    MIN_DELAY_S,
    SOURCES,
    FetchMode,
    Kind,
    Source,
    Target,
    crawl_command,
    get_source,
)

logger = logging.getLogger(__name__)

STALE_MARKERS = ["nebepasiekiamas", "skelbimas nerastas", "pašalintas iš sąrašo"]

BACKUP_DIR = Path("/app/backups")

# How much of a crawl's log the row keeps.
LOG_TAIL_LINES = 200
LOG_TAIL_CHARS = 20_000


@shared_task
def run_spider(
    name: str,
    property_type: str | None = None,
    trigger: str = ScrapeRun.Trigger.MANUAL,
    **spider_args: object,
) -> dict:
    """Run one registry source (see apps/listings/sources.py). Triggered from celery-beat.

    Spider args, delay and timeout come from the registry; keyword args here override
    registry spider args (e.g. `run_spider("domoplius", max_pages=1)`). With no
    `property_type`, every target switched on in settings.SCAN_PROPERTY_TYPES runs, one
    crawl (and one ScrapeRun) each. `trigger` is "beat" from the schedule (config/celery.py)
    and "manual" otherwise; it is recorded, not passed to the spider. Runs on the scraper
    worker, scrapers project at /app.
    """
    if trigger not in ScrapeRun.Trigger.values:
        raise ValueError(f"Unknown trigger {trigger!r}; use one of {ScrapeRun.Trigger.values}")
    source = get_source(name)
    if not source.built:
        raise ValueError(f"Source {name!r} is registered but its spider is not built yet")
    if property_type is None:
        targets = source.active_targets(settings.SCAN_PROPERTY_TYPES)
    else:
        targets = (source.target(property_type),)

    if not targets:
        logger.warning(
            "Source %s has no target for SCAN_PROPERTY_TYPES=%s; nothing to crawl",
            name,
            settings.SCAN_PROPERTY_TYPES,
        )
    crawls = [_crawl(source, target, spider_args, trigger) for target in targets]
    return {"spider": name, "crawls": crawls}


def _crawl(source: Source, target: Target, overrides: dict[str, object], trigger: str) -> dict:
    """One `scrapy crawl` subprocess. The row exists before the crawl starts, so a crawl
    that dies early, crashes or times out still leaves a failed run with its log."""
    run = ScrapeRun.objects.create(
        spider=source.spider,
        started_at=timezone.now(),
        trigger=trigger,
        args=source.crawl_args(target, overrides),
        start_url=clip_start_url(target.start_urls[0]),  # Target guarantees one
    )
    cmd = [*crawl_command(source, target, overrides), "-s", f"{RUN_ID_SETTING}={run.pk}"]
    logger.info("Starting spider (run %d): %s", run.pk, " ".join(cmd))
    report: dict = {"property_type": target.property_type, "run_id": run.pk, "timed_out": False}
    try:
        result = subprocess.run(
            cmd, cwd="/app", capture_output=True, text=True, timeout=source.timeout_s
        )
    except subprocess.TimeoutExpired as exc:
        # The killed crawl never reached spider_closed; close its row here and now.
        logger.error("Spider %s timed out after %d s", source.spider, source.timeout_s)
        _close_run(run.pk, _text(exc.stderr) or _text(exc.output), "timeout")
        return {
            **report,
            "timed_out": True,
            "exit_code": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    except BaseException as exc:
        # No crawl ran (scrapy missing, fork failed) or the worker is shutting down: close
        # the row as failed now rather than leave it running for the 6 h stale sweep.
        _close_run(run.pk, "", f"error: {type(exc).__name__}")
        raise

    if result.returncode != 0:
        logger.error(
            "Spider %s failed (exit %d): %s",
            source.spider,
            result.returncode,
            result.stderr[-500:],
        )
        unclosed_reason = f"exit {result.returncode}"
    else:
        logger.info("Spider %s completed successfully", source.spider)
        # Exit 0, yet the recorder never closed the row: the outcome is unknown.
        unclosed_reason = "unrecorded"
    # Scrapy logs to stderr; stdout only carries what a spider prints.
    _close_run(run.pk, result.stderr or result.stdout or "", unclosed_reason)
    return {
        **report,
        "exit_code": result.returncode,
        "stdout_tail": result.stdout[-200:] if result.stdout else "",
        "stderr_tail": result.stderr[-200:] if result.stderr else "",
    }


def _text(output: str | bytes | None) -> str:
    """TimeoutExpired carries bytes even with text=True; decode whatever it holds."""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output or ""


def log_tail(log: str) -> str:
    """The last lines of a crawl log, contact data scrubbed (dropped-item warnings print the
    item), at most LOG_TAIL_LINES lines and LOG_TAIL_CHARS characters."""
    tail = "\n".join(log.splitlines()[-LOG_TAIL_LINES:])
    return scrub_text(tail)[-LOG_TAIL_CHARS:]


def _close_run(run_id: int, log: str, unclosed_reason: str) -> None:
    """Keep the log tail; a row the recorder left running is failed with `unclosed_reason`."""
    ScrapeRun.objects.filter(pk=run_id).update(log_tail=log_tail(log))
    closed = ScrapeRun.objects.filter(pk=run_id, status=ScrapeRun.Status.RUNNING).update(
        status=ScrapeRun.Status.FAILED,
        finish_reason=unclosed_reason,
        finished_at=timezone.now(),
    )
    if closed:
        logger.error("Run %d never closed its row; marked failed (%s)", run_id, unclosed_reason)


@shared_task
def notify_scrape_problems() -> dict:
    """E-mail the principal about blocked, needs-human, failed or twice-empty scans.

    At most once per source per 24 h; see apps/listings/notify.py. Scheduled hourly."""
    from apps.listings.notify import send_problem_notifications

    return send_problem_notifications()


@shared_task
def nightly_db_backup() -> dict:
    """Dump the PostgreSQL database to a timestamped file in /app/backups/.

    Uses pg_dump with the DATABASE_URL from Django settings.
    Keeps the last 7 backups and removes older ones.
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    db_settings = settings.DATABASES["default"]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"realestate_{timestamp}.sql.gz"
    filepath = BACKUP_DIR / filename

    env = {
        "PGPASSWORD": db_settings.get("PASSWORD", ""),
    }

    cmd = (
        f"pg_dump"
        f" -h {db_settings['HOST']}"
        f" -p {db_settings.get('PORT', '5432')}"
        f" -U {db_settings['USER']}"
        f" -d {db_settings['NAME']}"
        f" --no-owner --no-acl"
        f" | gzip > {filepath}"
    )

    logger.info("Starting database backup to %s", filepath)

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=1800,
            env={**subprocess.os.environ, **env},
        )

        if result.returncode != 0:
            logger.error("Database backup failed: %s", result.stderr[-500:])
            return {"status": "error", "message": result.stderr[-500:]}

        size_mb = round(filepath.stat().st_size / (1024 * 1024), 2)
        logger.info("Database backup completed: %s (%.2f MB)", filename, size_mb)

        # Rotate: keep last 7 backups
        backups = sorted(BACKUP_DIR.glob("realestate_*.sql.gz"))
        for old_backup in backups[:-7]:
            old_backup.unlink()
            logger.info("Removed old backup: %s", old_backup.name)

        return {"status": "ok", "file": filename, "size_mb": size_mb}
    except subprocess.TimeoutExpired:
        logger.error("Database backup timed out after 30 minutes")
        return {"status": "error", "message": "Backup timed out"}
    except Exception as exc:
        logger.exception("Database backup failed with unexpected error")
        return {"status": "error", "message": str(exc)}


# --- Validity checker ---------------------------------------------------------------------

REQUEST_TIMEOUT_S = 10
# One run never goes past this many listings, whatever the caller asks for (3 s apart, that is
# five minutes of polite traffic at most).
MAX_VALIDITY_BATCH = 100
# A source whose latest finished scan was blocked within this long ago is not checked.
BLOCKED_SCAN_WINDOW = timedelta(days=7)
# After this many blocked answers from one source in one run, the run leaves that source alone.
STOP_SOURCE_AFTER_BLOCKS = 2
STATE_TEXT_CHARS = 1500

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

AD_GONE = judge.Question.yes_no(
    "ad_is_gone",
    "`final_url` is where a request for `listing_url` ended up. Is this page a notice that the "
    "ad is no longer available (removed, expired, sold), or a search, category or home page "
    "shown in its place, rather than the ad itself?",
    yes="The page says the ad is gone, or is a generic page that replaced it",
    no="The page is the ad itself, even if its URL changed",
)


class _FetchError(Exception):
    """The request failed before any answer: network, TLS, timeout."""


@dataclass(frozen=True)
class _Page:
    status: int
    url: str  # where the request ended up, after redirects
    text: str


def _fetch(source: Source | None, url: str) -> _Page:
    """GET `url` the way its source's scraper does: curl_cffi's browser TLS fingerprint for
    FetchMode.CURL_CFFI sources, plain requests for the rest. Never a browser."""
    if source is not None and source.fetch_mode == FetchMode.CURL_CFFI:
        try:
            from curl_cffi import requests as curl_requests
            from curl_cffi.requests import exceptions as curl_exceptions
        except ImportError:
            logger.warning("curl_cffi is not installed here; %s is fetched with requests", url)
        else:
            try:
                reply = curl_requests.get(
                    url, impersonate="chrome", timeout=REQUEST_TIMEOUT_S, allow_redirects=True
                )
            except curl_exceptions.RequestException as exc:
                raise _FetchError(str(exc)) from exc
            return _Page(reply.status_code, str(reply.url), reply.text)
    try:
        reply = requests.get(
            url,
            timeout=REQUEST_TIMEOUT_S,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; NTBot/1.0)"},
        )
    except requests.RequestException as exc:
        raise _FetchError(str(exc)) from exc
    return _Page(reply.status_code, str(reply.url), reply.text)


class _Pacer:
    """Keeps requests to one source at least its registry delay apart (never under 3 s)."""

    def __init__(self) -> None:
        self._last_request_end: dict[str, float] = {}

    def wait(self, spider: str, delay_s: float) -> None:
        last = self._last_request_end.get(spider)
        if last is not None and (remaining := delay_s - (time.monotonic() - last)) > 0:
            time.sleep(remaining)

    def done(self, spider: str) -> None:
        self._last_request_end[spider] = time.monotonic()


def _registered(spider: str) -> Source | None:
    try:
        return get_source(spider)
    except ValueError:  # a listing from a source that left the registry
        return None


def _skipped_sources() -> dict[str, str]:
    """Spiders whose listings this run must not request, with the reason.

    A source is skipped when it cannot be checked from here (its data is behind a login, or
    its pages need a real browser) or when its latest finished scan was blocked: a block page
    must never read as "the ad is still up"."""
    skipped = {}
    for source in SOURCES:
        if source.needs_login:
            skipped[source.spider] = "needs login"
        elif source.kind == Kind.LISTING and source.fetch_mode == FetchMode.PLAYWRIGHT:
            skipped[source.spider] = "needs a browser"
    since = timezone.now() - BLOCKED_SCAN_WINDOW
    latest = (
        ScrapeRun.objects.filter(started_at__gte=since)
        .exclude(status=ScrapeRun.Status.RUNNING)
        .order_by("spider", "-started_at")
        .distinct("spider")
        .values_list("spider", "status")
    )
    for spider, status in latest:
        if status == ScrapeRun.Status.BLOCKED:
            skipped.setdefault(spider, "latest scan blocked")
    return skipped


def _moved(original: str, final: str) -> bool:
    """Did the request end on a different path than it asked for?"""

    def path(url: str) -> str:
        return urlsplit(url).path.rstrip("/") or "/"

    return path(original) != path(final)


def _page_state(listing_url: str, page: _Page) -> dict:
    title = TITLE_RE.search(page.text)
    visible = _TAG_RE.sub(" ", _SCRIPT_STYLE_RE.sub(" ", page.text))
    return {
        "listing_url": listing_url,
        "final_url": page.url,
        "title": " ".join(title.group(1).split()) if title else "",
        "text": " ".join(visible.split())[:STATE_TEXT_CHARS],
    }


def _ad_is_gone(listing_id: int, listing_url: str, page: _Page) -> bool:
    """Ask Jev (apps/classifier/judge.py) whether the page a request was redirected to is the
    ad's removal notice. Only an explicit yes counts. With JEV_MODE=off the fallback abstains,
    so nothing is deactivated on a hunch."""
    result = judge.ask(
        "validity",
        listing_id,
        _page_state(listing_url, page),
        [AD_GONE],
        lambda pending: {q.name: None for q in pending},
    )
    return result[AD_GONE.name].value is True


def _judge_page(listing_id: int, source_url: str, page: _Page) -> str:
    """The Listing.CheckResult for one fetched page."""
    moved = _moved(source_url, page.url)
    if classify_response(page.status, page.url, page.text, redirected=moved) is not None:
        return Listing.CheckResult.BLOCKED
    if page.status in (404, 410):
        return Listing.CheckResult.GONE
    if page.status != 200:
        return Listing.CheckResult.ERROR  # 5xx, odd 3xx/4xx: says nothing about the ad
    if any(marker in page.text.lower() for marker in STALE_MARKERS):
        return Listing.CheckResult.STALE
    if moved and _ad_is_gone(listing_id, source_url, page):
        return Listing.CheckResult.STALE
    return Listing.CheckResult.OK


@shared_task
def check_listing_validity(batch_size: int = 50) -> dict:
    """Check that active listings are still up on their source sites, a few per run.

    Every attempt stamps `last_checked_at` and `last_check_result`, and the next run takes the
    never-checked and longest-ago listings first, so successive runs walk the whole table.
    A listing is deactivated on a 404/410 or a "removed" marker (or Jev's yes, for a page the
    request was redirected to). A block page, a 401/403/429 or a failed request deactivates
    nothing and verifies nothing: the result is "blocked" or "error". Sources that are blocked
    (latest scan blocked, or two blocked answers this run), need a login or need a browser are
    left alone. Requests to one source are spaced by its registry delay (3 s or more) and
    follow its fetch mode (curl_cffi where the scraper needs it). `batch_size` is capped at
    MAX_VALIDITY_BATCH.
    """
    batch_size = max(0, min(batch_size, MAX_VALIDITY_BATCH))
    skipped = _skipped_sources()
    listings = list(
        Listing.objects.filter(is_active=True)
        .exclude(source__in=skipped)
        .order_by(F("last_checked_at").asc(nulls_first=True), "pk")
        .only("pk", "source", "source_url")[:batch_size]
    )

    pacer = _Pacer()
    blocks: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    for listing in listings:
        spider = listing.source
        if blocks[spider] >= STOP_SOURCE_AFTER_BLOCKS:
            skipped.setdefault(spider, "blocked during this run")
            continue
        source = _registered(spider)
        pacer.wait(spider, max(MIN_DELAY_S, source.delay_s if source else MIN_DELAY_S))
        try:
            page = _fetch(source, listing.source_url)
            result = _judge_page(listing.pk, listing.source_url, page)
        except _FetchError as exc:
            logger.info("Validity check of %s failed: %s", listing.source_url, exc)
            result = Listing.CheckResult.ERROR
        finally:
            pacer.done(spider)
        if result == Listing.CheckResult.BLOCKED:
            blocks[spider] += 1
        counts[result] += 1
        # update(), not save(): last_seen_at is the scraper's, and auto_now would move it.
        fields = {"last_checked_at": timezone.now(), "last_check_result": result}
        if result in (Listing.CheckResult.GONE, Listing.CheckResult.STALE):
            fields["is_active"] = False
        Listing.objects.filter(pk=listing.pk).update(**fields)

    checked = sum(counts.values())
    deactivated = counts[Listing.CheckResult.GONE] + counts[Listing.CheckResult.STALE]
    logger.info(
        "Validity check: %d checked, %d deactivated, %d blocked, %d errors; skipped sources %s",
        checked,
        deactivated,
        counts[Listing.CheckResult.BLOCKED],
        counts[Listing.CheckResult.ERROR],
        skipped or "none",
    )
    return {
        "checked": checked,
        "deactivated": deactivated,
        "blocked": counts[Listing.CheckResult.BLOCKED],
        "errors": counts[Listing.CheckResult.ERROR],
        "skipped_sources": skipped,
    }
