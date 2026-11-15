"""Turn Scrapy crawl stats into ScrapeRun fields. Pure functions, used by the scrapers'
ScrapeRunRecorder extension and unit-tested here.

Which spiders exist, and which feed Listing, is the source registry's job
(apps/listings/sources.py)."""

from apps.listings.models import ScrapeRun


def normalize_drop_reason(message: str) -> str:
    """'Cannot geocode ... for: <address>' → 'Cannot geocode ... for', so reasons group.
    A one-word detail (a field name) is kept: 'Missing required field: price'."""
    head, sep, detail = str(message).partition(": ")
    if sep and len(detail.split()) == 1:
        return f"{head}: {detail}"[:120]
    return head[:120]


def summarize_stats(stats: dict) -> dict:
    """ScrapeRun field values from a finished crawl's stats dict."""
    saved = stats.get("item_scraped_count", 0)
    dropped = stats.get("item_dropped_count", 0)
    finish_reason = stats.get("finish_reason", "")
    forbidden = sum(
        stats.get(f"downloader/response_status_count/{code}", 0) for code in (401, 403, 429)
    )

    if finish_reason != "finished":
        status = ScrapeRun.Status.FAILED
    elif saved == 0 and forbidden:
        status = ScrapeRun.Status.BLOCKED
    elif saved == 0:
        status = ScrapeRun.Status.EMPTY
    else:
        status = ScrapeRun.Status.OK

    return {
        "status": status,
        "finish_reason": str(finish_reason)[:64],
        "pages_crawled": stats.get("response_received_count", 0),
        "items_found": saved + dropped,
        "items_saved": saved,
        "items_new": stats.get("nt/items_new", 0),
        "items_updated": stats.get("nt/items_updated", 0),
        "items_unchanged": stats.get("nt/items_unchanged", 0),
        "items_dropped": dropped,
        "errors": stats.get("log_count/ERROR", 0),
    }
