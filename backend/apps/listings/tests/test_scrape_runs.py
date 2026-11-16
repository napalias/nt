from datetime import timedelta

import pytest
from django.contrib.gis.geos import Point
from django.test import Client
from django.utils import timezone
from model_bakery import baker

from apps.listings.models import Listing, ScrapeRun
from apps.listings.scrape_runs import normalize_drop_reason, summarize_stats
from apps.listings.sources import SOURCES


class TestSummarizeStats:
    def test_normal_run_maps_counts(self):
        stats = {
            "finish_reason": "finished",
            "response_received_count": 22,
            "item_scraped_count": 20,
            "item_dropped_count": 2,
            "nt/items_new": 15,
            "nt/items_updated": 5,
            "log_count/ERROR": 1,
        }
        out = summarize_stats(stats)
        assert out["status"] == ScrapeRun.Status.PARTIAL  # saved, but logged an error (#8)
        assert out["pages_crawled"] == 22
        assert out["items_found"] == 22  # saved + dropped: everything the spider extracted
        assert out["items_saved"] == 20
        assert out["items_new"] == 15
        assert out["items_updated"] == 5
        assert out["items_dropped"] == 2
        assert out["errors"] == 1
        assert out["finish_reason"] == "finished"

    def test_forbidden_responses_with_nothing_saved_is_blocked(self):
        stats = {
            "finish_reason": "finished",
            "response_received_count": 1,
            "downloader/response_status_count/403": 1,
        }
        assert summarize_stats(stats)["status"] == ScrapeRun.Status.BLOCKED

    def test_no_items_is_empty(self):
        stats = {"finish_reason": "finished", "response_received_count": 0}
        assert summarize_stats(stats)["status"] == ScrapeRun.Status.EMPTY

    def test_abnormal_finish_is_failed(self):
        stats = {"finish_reason": "shutdown", "item_scraped_count": 3}
        assert summarize_stats(stats)["status"] == ScrapeRun.Status.FAILED


class TestNormalizeDropReason:
    def test_strips_item_specific_detail(self):
        msg = "Cannot geocode and no fallback for: Klaipėdos r. sav., Stančiuose"
        assert normalize_drop_reason(msg) == "Cannot geocode and no fallback for"

    def test_keeps_single_word_detail(self):
        assert normalize_drop_reason("Missing required field: price") == (
            "Missing required field: price"
        )


def _run(spider: str, minutes_ago: int, **kw) -> ScrapeRun:
    started = timezone.now() - timedelta(minutes=minutes_ago)
    return baker.make(
        ScrapeRun,
        spider=spider,
        started_at=started,
        finished_at=started + timedelta(minutes=5),
        **kw,
    )


@pytest.mark.django_db
class TestScrapeRunsApi:
    def test_runs_listed_newest_first(self):
        _run("domoplius", 60, status=ScrapeRun.Status.OK, items_new=3)
        _run("aruodas", 10, status=ScrapeRun.Status.BLOCKED)
        data = Client().get("/api/scrapes/runs").json()
        assert [r["spider"] for r in data] == ["aruodas", "domoplius"]
        assert data[1]["items_new"] == 3
        assert data[0]["status"] == "blocked"

    def test_runs_filter_by_spider(self):
        _run("domoplius", 60)
        _run("aruodas", 10)
        data = Client().get("/api/scrapes/runs", {"spider": "domoplius"}).json()
        assert [r["spider"] for r in data] == ["domoplius"]

    def test_runs_limit_is_clamped_to_1_to_500(self):
        for minutes in range(1, 502):
            _run("domoplius", minutes)
        # A negative limit used to slice with a negative index and answer 500.
        assert len(Client().get("/api/scrapes/runs", {"limit": -5}).json()) == 1
        assert len(Client().get("/api/scrapes/runs", {"limit": 0}).json()) == 1
        assert len(Client().get("/api/scrapes/runs", {"limit": 10_000}).json()) == 500
        assert len(Client().get("/api/scrapes/runs", {"limit": 7}).json()) == 7

    def test_summary_has_every_spider_even_never_run(self):
        _run("domoplius", 60, status=ScrapeRun.Status.OK, items_new=4)
        _run("domoplius", 30, status=ScrapeRun.Status.EMPTY)
        baker.make(Listing, source="domoplius", location=Point(21.2, 55.9, srid=4326))
        data = {row["spider"]: row for row in Client().get("/api/scrapes/summary").json()}
        assert list(data) == [s.spider for s in SOURCES]  # every source, registry order
        assert data["domoplius"]["last_run"]["status"] == "empty"
        assert data["domoplius"]["last_ok_at"] is not None
        assert data["domoplius"]["listings_total"] == 1
        assert data["domoplius"]["new_7d"] == 1
        assert data["tpdris"]["last_run"] is None

    def test_summary_carries_registry_config(self):
        data = {row["spider"]: row for row in Client().get("/api/scrapes/summary").json()}
        unbuilt = data["ntseptynetas"]  # registered, no spider (site has no listings), never run
        assert unbuilt["state"] == "not_built"
        assert unbuilt["enabled"] is False
        assert unbuilt["label"] == "NT Septynetas"
        assert unbuilt["kind"] == "listing"
        assert unbuilt["last_run"] is None
        assert unbuilt["runs_7d"] == 0
        assert unbuilt["listings_total"] == 0
        assert data["rasknt"]["state"] == "needs_login"
        assert data["rasknt"]["enabled"] is False
        assert "login" in data["rasknt"]["note"].lower()
        assert data["realu"]["state"] == "scheduled"
        assert data["domoplius"]["state"] == "scheduled"
        assert data["domoplius"]["fetch_mode"] == "plain"
        assert data["domoplius"]["schedule"] == "0 6 * * *"
        assert data["domoplius"]["start_urls"]
        assert data["aruodas"]["state"] == "paused"
        assert data["aruodas"]["note"]
        assert data["pamario-nt"]["delay_s"] == 10
        assert data["tpdris"]["kind"] == "gov"
        assert data["tpdris"]["listings_total"] is None

    def test_new_listings_of_a_run(self):
        run = baker.make(
            ScrapeRun,
            spider="domoplius",
            started_at=timezone.now() - timedelta(minutes=1),
            finished_at=timezone.now() + timedelta(minutes=1),
        )
        baker.make(Listing, source="domoplius", title="Naujas namas",
                   location=Point(21.2, 55.9, srid=4326))
        baker.make(Listing, source="skelbiu", location=Point(21.2, 55.9, srid=4326))
        data = Client().get(f"/api/scrapes/runs/{run.id}/new").json()
        assert [d["title"] for d in data] == ["Naujas namas"]
        assert "source_url" in data[0]
