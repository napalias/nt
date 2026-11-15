"""Scan history for the /scans page: which sites ran, what they found, what was new."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Schema

from apps.listings.models import Listing, ScrapeRun
from apps.listings.sources import SOURCES, Kind

router = Router(tags=["scrapes"])


class ScrapeRunOut(Schema):
    id: int
    spider: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    finish_reason: str
    pages_crawled: int
    items_found: int
    items_saved: int
    items_new: int
    items_updated: int
    items_dropped: int
    errors: int
    drop_reasons: dict[str, int]


class SpiderSummaryOut(Schema):
    spider: str
    # From the source registry, so never-run and not-built sources still show.
    label: str
    kind: str
    state: str  # scheduled | paused | not_built
    enabled: bool
    fetch_mode: str
    delay_s: float
    schedule: str  # cron: minute hour day month weekday (Europe/Vilnius)
    start_urls: list[str]
    note: str
    last_run: ScrapeRunOut | None
    last_ok_at: datetime | None
    runs_7d: int
    listings_total: int | None
    new_7d: int | None


class NewListingOut(Schema):
    id: int
    title: str
    source_url: str
    price: float | None
    city: str
    address_raw: str
    first_seen_at: datetime


@router.get("/runs", response=list[ScrapeRunOut])
def list_runs(request, spider: str | None = None, limit: int = 50):
    runs = ScrapeRun.objects.all()
    if spider:
        runs = runs.filter(spider=spider)
    return runs[: max(1, min(limit, 500))]


@router.get("/summary", response=list[SpiderSummaryOut])
def summary(request):
    week_ago = timezone.now() - timedelta(days=7)
    rows = []
    for source in SOURCES:
        runs = ScrapeRun.objects.filter(spider=source.spider)
        last_ok = runs.filter(status=ScrapeRun.Status.OK).first()
        is_listing = source.kind == Kind.LISTING
        listings = Listing.objects.filter(source=source.spider)
        rows.append({
            "spider": source.spider,
            "label": source.label,
            "kind": source.kind.value,
            "state": source.state.value,
            "enabled": source.enabled,
            "fetch_mode": source.fetch_mode.value,
            "delay_s": source.delay_s,
            "schedule": str(source.schedule),
            "start_urls": list(source.start_urls),
            "note": source.note,
            "last_run": runs.first(),
            "last_ok_at": last_ok.started_at if last_ok else None,
            "runs_7d": runs.filter(started_at__gte=week_ago).count(),
            "listings_total": listings.count() if is_listing else None,
            "new_7d": listings.filter(first_seen_at__gte=week_ago).count() if is_listing else None,
        })
    return rows


@router.get("/runs/{run_id}/new", response=list[NewListingOut])
def run_new_listings(request, run_id: int):
    """Listings first seen while this run was going (new ads it brought in)."""
    run = get_object_or_404(ScrapeRun, pk=run_id)
    end = run.finished_at or timezone.now()
    return Listing.objects.filter(
        source=run.spider, first_seen_at__gte=run.started_at, first_seen_at__lte=end
    ).order_by("-first_seen_at")[:200]
