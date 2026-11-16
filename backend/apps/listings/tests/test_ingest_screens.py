"""The listings side of Jev's scraping screens (#18): the expired-page check in the validity
task, the mixed-feed flag in the registry, and screened drops in the run summary.

Jev is a fake; nothing reaches the network.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.gis.geos import Point
from model_bakery import baker

from apps.classifier import judge
from apps.classifier.models import JudgmentLog
from apps.listings.models import Listing, ScrapeRun
from apps.listings.scrape_runs import SCREENED_STAT, summarize_stats
from apps.listings.sources import SOURCES
from apps.listings.tasks import check_listing_validity


class FakeJev:
    def __init__(self, p_expired):
        self.p_expired = p_expired
        self.calls = 0

    def system_one(self, *, state, questions, model, timeout):
        self.calls += 1
        answers = {n: {"type": "noul", "noul": self.p_expired} for n in questions}
        return {"model": model, "answers": answers, "usage": {}}


def listing_with_page(body):
    listing = baker.make(
        Listing,
        is_active=True,
        location=Point(21.2420, 55.8835, srid=4326),
        address_raw="Kretinga",
        source_url="https://example.invalid/skelbimas/1",
    )
    # The page answered at the URL asked for: not a redirect (that path asks ad_is_gone instead).
    response = MagicMock(status_code=200, text=body, url=listing.source_url)
    return listing, response


@pytest.mark.django_db
class TestValidityExpiredQuestion:
    LIVE = "<html><h1>Parduodamas namas</h1></html>"
    GONE = "<html><h1>Skelbimas nebepasiekiamas</h1></html>"

    def run(self, response):
        with (
            patch("apps.listings.tasks.requests.get", return_value=response),
            patch("apps.listings.tasks.time.sleep"),
        ):
            return check_listing_validity(batch_size=10)

    def jev(self, settings, monkeypatch, mode, p):
        settings.JEV_MODE = mode
        settings.JEV_MODEL = "jev-1.14.0"
        fake = FakeJev(p)
        monkeypatch.setattr(judge, "get_default_client", lambda: fake)
        return fake

    def test_off_markers_decide_and_jev_is_not_asked(self, settings, monkeypatch):
        fake = self.jev(settings, monkeypatch, "off", 0.99)
        listing, response = listing_with_page(self.LIVE)
        assert self.run(response)["deactivated"] == 0
        assert fake.calls == 0 and JudgmentLog.objects.count() == 0
        listing.refresh_from_db()
        assert listing.is_active

    def test_shadow_keeps_the_marker_verdict_and_logs_jev(self, settings, monkeypatch):
        self.jev(settings, monkeypatch, "shadow", 0.99)
        listing, response = listing_with_page(self.LIVE)
        assert self.run(response)["deactivated"] == 0  # Jev says gone; shadow does not act
        listing.refresh_from_db()
        assert listing.is_active
        row = JudgmentLog.objects.get(question="page_expired")
        assert row.subject_id == str(listing.pk) and row.jev_answer is True

    def test_on_jev_deactivates_a_page_the_markers_miss(self, settings, monkeypatch):
        self.jev(settings, monkeypatch, "on", 0.99)
        listing, response = listing_with_page(self.LIVE)
        assert self.run(response)["deactivated"] == 1
        listing.refresh_from_db()
        assert not listing.is_active

    def test_on_jev_spares_a_listing_whose_page_only_mentions_a_marker(self, settings, monkeypatch):
        self.jev(settings, monkeypatch, "on", 0.01)
        listing, response = listing_with_page(self.GONE)
        assert self.run(response)["deactivated"] == 0
        listing.refresh_from_db()
        assert listing.is_active


class TestMixedFeedFlag:
    def test_only_the_three_mixed_feeds_are_flagged(self):
        flagged = {s.spider for s in SOURCES if s.mixed_feed}
        assert flagged == {"skelbiu", "alio", "kretingosskelbimai"}


class TestScreenedDropsAreNotTrouble:
    BASE = {"finish_reason": "finished", "response_received_count": 12}

    def test_screened_ads_do_not_make_a_run_partial(self):
        stats = {**self.BASE, "item_scraped_count": 4, "item_dropped_count": 8, SCREENED_STAT: 8}
        out = summarize_stats(stats)
        assert out["status"] == ScrapeRun.Status.OK
        assert out["items_dropped"] == 8  # still counted, with their reasons, on /scans

    def test_other_drops_still_count(self):
        stats = {**self.BASE, "item_scraped_count": 4, "item_dropped_count": 8, SCREENED_STAT: 3}
        assert summarize_stats(stats)["status"] == ScrapeRun.Status.PARTIAL
