"""Turn Scrapy crawl stats into ScrapeRun fields. Pure functions, used by the scrapers'
ScrapeRunRecorder extension and unit-tested here.

Which spiders exist, and which feed Listing, is the source registry's job
(apps/listings/sources.py). Block kinds come from the scrapers' BlockDetectionMiddleware
through the stats named in apps/listings/blocks.py."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from apps.listings.blocks import (
    FINISH_REASON_KINDS,
    NEEDS_HUMAN,
    NEEDS_HUMAN_STAT,
    PRIORITY,
    STATUS_KINDS,
    BlockKind,
    stat_key,
)
from apps.listings.models import ScrapeRun

# Scrapy setting carrying the ScrapeRun id from run_spider (apps/listings/tasks.py) to the
# crawl's ScrapeRunRecorder, which fills in that row instead of making its own.
RUN_ID_SETTING = "NT_SCRAPE_RUN_ID"

# A run that saved items but dropped more than this share of what it found is partial.
MAX_DROPPED_SHARE = 0.3


def clip_start_url(url: str) -> str:
    """A start URL cut to what ScrapeRun.start_url holds. Every writer goes through this,
    because `.update()` and `create()` on Postgres fail on an over-long value instead."""
    return url[: ScrapeRun.START_URL_MAX_LENGTH]


def normalize_drop_reason(message: str) -> str:
    """'Cannot geocode ... for: <address>' → 'Cannot geocode ... for', so reasons group.
    A one-word detail (a field name) is kept: 'Missing required field: price'."""
    head, sep, detail = str(message).partition(": ")
    if sep and len(detail.split()) == 1:
        return f"{head}: {detail}"[:120]
    return head[:120]


def _status_count(stats: Mapping, code: int) -> int:
    return stats.get(f"downloader/response_status_count/{code}", 0)


def block_kind_from_stats(stats: Mapping) -> BlockKind | None:
    """The most actionable block kind the crawl ran into, or None.

    Kinds the middleware recorded come first; bare 401/403/429 counts and a blocking close
    reason (robotstxt_denied) are the fallback, so stats from a crawl without the
    middleware still name the block."""
    recorded = {kind for kind in BlockKind if stats.get(stat_key(kind), 0) > 0}
    recorded |= {kind for code, kind in STATUS_KINDS.items() if _status_count(stats, code)}
    if (closed_by := FINISH_REASON_KINDS.get(stats.get("finish_reason", ""))) is not None:
        recorded.add(closed_by)
    return next((kind for kind in PRIORITY if kind in recorded), None)


def summarize_stats(stats: Mapping) -> dict:
    """ScrapeRun field values from a finished crawl's stats dict.

    failed: the crawl did not finish normally, and not because a site blocked it.
    blocked: nothing saved and a block was hit (this includes Scrapy closing the crawl with
    "robotstxt_denied"). empty: nothing saved, no block. partial: saved some, but logged
    errors, got 401/403/429 answers or a block page, or dropped more than 30% of what it
    found. ok: the rest.
    """
    saved = stats.get("item_scraped_count", 0)
    dropped = stats.get("item_dropped_count", 0)
    found = saved + dropped
    errors = stats.get("log_count/ERROR", 0)
    finish_reason = stats.get("finish_reason", "")
    block_kind = block_kind_from_stats(stats)
    dropped_too_many = found > 0 and dropped / found > MAX_DROPPED_SHARE
    crashed = finish_reason != "finished" and finish_reason not in FINISH_REASON_KINDS

    if crashed:
        status = ScrapeRun.Status.FAILED
    elif saved == 0 and block_kind is not None:
        status = ScrapeRun.Status.BLOCKED
    elif saved == 0:
        status = ScrapeRun.Status.EMPTY
    elif errors or block_kind is not None or dropped_too_many:
        status = ScrapeRun.Status.PARTIAL
    else:
        status = ScrapeRun.Status.OK

    return {
        "status": status,
        "finish_reason": str(finish_reason)[:64],
        "pages_crawled": stats.get("response_received_count", 0),
        "links_seen": stats.get("nt/links_seen", stats.get("scheduler/enqueued", 0)),
        "items_found": found,
        "items_saved": saved,
        "items_new": stats.get("nt/items_new", 0),
        "items_updated": stats.get("nt/items_updated", 0),
        "items_unchanged": stats.get("nt/items_unchanged", 0),
        "items_dropped": dropped,
        "errors": errors,
        "block_kind": block_kind.value if block_kind else "",
        "needs_human": block_kind in NEEDS_HUMAN or bool(stats.get(NEEDS_HUMAN_STAT)),
    }


def spider_args_from_argv(argv: Sequence[str]) -> dict[str, str] | None:
    """Every `-a NAME=VALUE` of a `scrapy crawl` command line; None if argv is not one.

    Scrapy keeps spider arguments only as spider attributes, so the command line is the one
    complete record of what a hand-started crawl was given. `-aNAME=VALUE` is accepted
    too; a pair without "=" is not a spider argument and is skipped.
    """
    if "crawl" not in argv:
        return None
    args: dict[str, str] = {}
    parts = list(argv)
    i = 0
    while i < len(parts):
        part = parts[i]
        if part == "-a":
            pair = parts[i + 1] if i + 1 < len(parts) else ""
            i += 2  # the value is consumed, never read as an option itself
        elif part.startswith("-a") and len(part) > 2:
            pair = part[2:]
            i += 1
        else:
            i += 1
            continue
        name, sep, value = pair.partition("=")
        if sep and name:
            args[name] = value
    return args
