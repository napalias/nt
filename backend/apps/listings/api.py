"""Scan history for the /scans page: which sites ran, what they found, what was new."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Schema

from apps.listings.models import Listing, ScrapeRun
from apps.listings.scrape_runs import LISTING_SPIDERS, SPIDERS

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
    for spider in SPIDERS:
        runs = ScrapeRun.objects.filter(spider=spider)
        last_ok = runs.filter(status=ScrapeRun.Status.OK).first()
        is_listing = spider in LISTING_SPIDERS
        listings = Listing.objects.filter(source=spider)
        rows.append({
            "spider": spider,
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
