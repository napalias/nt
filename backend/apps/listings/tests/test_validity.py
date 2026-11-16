import itertools
import sys
import types
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.contrib.gis.geos import Point
from django.utils import timezone
from model_bakery import baker

from apps.classifier import judge
from apps.classifier.models import JudgmentLog
from apps.listings import tasks
from apps.listings.models import Listing, ScrapeRun
from apps.listings.tasks import MAX_VALIDITY_BATCH, check_listing_validity

_ids = itertools.count(1)

NORMAL_PAGE = (
    "<html><head><title>Namas Kretingoje</title></head><body>Namas, 200000 EUR</body></html>"
)
PERIMETERX_PAGE = (
    "<html><head><title>Access to this page has been denied</title></head>"
    '<body><div id="px-captcha"></div></body></html>'
)


class FakeClock:
    """Stands in for the `time` module of apps.listings.tasks: sleeping advances the clock."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock():
    fake = FakeClock()
    with patch.object(tasks, "time", fake):
        yield fake


def _listing(source: str = "domoplius", **kwargs) -> Listing:
    n = next(_ids)
    defaults = {
        "is_active": True,
        "source": source,
        "source_id": f"{source}-{n}",
        "source_url": f"https://{source}.example.invalid/skelbimai/{n}",
        "location": Point(21.2420, 55.8835, srid=4326),
        "address_raw": "Kretinga",
    }
    defaults.update(kwargs)
    return baker.make(Listing, **defaults)


def _reply(status: int = 200, text: str = NORMAL_PAGE, url: str | None = None) -> MagicMock:
    reply = MagicMock()
    reply.status_code = status
    reply.text = text
    reply.url = url
    return reply


@pytest.fixture
def get(clock):
    """requests.get, answering 200 with a normal page and echoing the asked URL back as the
    final one. `get.answers[url] = reply` overrides one URL; `get.urls` is what was asked."""
    answers: dict[str, MagicMock] = {}
    asked: list[str] = []

    def fake_get(url, **kwargs):
        asked.append(url)
        reply = answers.get(url) or _reply()
        if reply.url is None:
            reply.url = url
        return reply

    with patch("apps.listings.tasks.requests.get", side_effect=fake_get) as mock:
        mock.answers = answers
        mock.urls = asked
        yield mock


@pytest.mark.django_db
class TestVerdicts:
    @pytest.mark.parametrize("status", [404, 410])
    def test_gone_status_deactivates(self, get, status):
        listing = _listing()
        get.answers[listing.source_url] = _reply(status, "")

        result = check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.is_active is False
        assert listing.last_check_result == Listing.CheckResult.GONE
        assert result["checked"] == 1
        assert result["deactivated"] == 1

    @pytest.mark.parametrize(
        "body",
        [
            "<html><body>Šis skelbimas nebepasiekiamas</body></html>",
            "<html><body>Skelbimas nerastas arba pašalintas</body></html>",
        ],
    )
    def test_stale_marker_deactivates(self, get, body):
        listing = _listing()
        get.answers[listing.source_url] = _reply(200, body)

        result = check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.is_active is False
        assert listing.last_check_result == Listing.CheckResult.STALE
        assert result["deactivated"] == 1

    def test_normal_page_keeps_listing_active(self, get):
        listing = _listing()

        result = check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.is_active is True
        assert listing.last_check_result == Listing.CheckResult.OK
        assert result == {
            "checked": 1,
            "deactivated": 0,
            "blocked": 0,
            "errors": 0,
            "skipped_sources": result["skipped_sources"],
        }

    def test_network_error_deactivates_nothing_but_is_recorded(self, get):
        listing = _listing()
        get.side_effect = requests.ConnectionError("refused")

        result = check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.is_active is True
        assert listing.last_check_result == Listing.CheckResult.ERROR
        assert listing.last_checked_at is not None
        assert result["checked"] == 1
        assert result["errors"] == 1

    def test_server_error_deactivates_nothing(self, get):
        listing = _listing()
        get.answers[listing.source_url] = _reply(503, "Service Unavailable")

        check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.is_active is True
        assert listing.last_check_result == Listing.CheckResult.ERROR

    def test_mixed_results(self, get):
        fine = _listing()
        stale = _listing()
        gone = _listing()
        get.answers[stale.source_url] = _reply(200, "<html>Šis skelbimas nebepasiekiamas</html>")
        get.answers[gone.source_url] = _reply(404, "")

        result = check_listing_validity(batch_size=10)

        for listing in (fine, stale, gone):
            listing.refresh_from_db()
        assert (fine.is_active, stale.is_active, gone.is_active) == (True, False, False)
        assert result["checked"] == 3
        assert result["deactivated"] == 2

    def test_inactive_listings_are_not_checked(self, get):
        _listing(is_active=False)
        _listing()

        result = check_listing_validity(batch_size=10)

        assert result["checked"] == 1
        assert len(get.urls) == 1

    def test_does_not_move_last_seen_at(self, get):
        listing = _listing()
        old = timezone.now() - timedelta(days=3)
        Listing.objects.filter(pk=listing.pk).update(last_seen_at=old)

        check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.last_seen_at == old


@pytest.mark.django_db
class TestRotation:
    def test_every_check_sets_last_checked_at(self, get):
        listings = [_listing() for _ in range(3)]
        get.answers[listings[1].source_url] = _reply(404, "")
        get.answers[listings[2].source_url] = _reply(403, "Forbidden")
        before = timezone.now()

        check_listing_validity(batch_size=10)

        for listing in listings:
            listing.refresh_from_db()
            assert listing.last_checked_at is not None
            assert listing.last_checked_at >= before
        assert [x.last_check_result for x in listings] == ["ok", "gone", "blocked"]

    def test_two_consecutive_runs_check_different_listings(self, get):
        listings = [_listing() for _ in range(4)]
        # last_seen_at is the scraper's clock; the order must ignore it.
        for age, listing in enumerate(listings):
            Listing.objects.filter(pk=listing.pk).update(
                last_seen_at=timezone.now() - timedelta(days=age)
            )

        check_listing_validity(batch_size=2)
        first_run = list(get.urls)
        check_listing_validity(batch_size=2)
        second_run = get.urls[len(first_run) :]

        assert len(first_run) == 2
        assert len(second_run) == 2
        assert set(first_run).isdisjoint(second_run)
        assert set(first_run) | set(second_run) == {x.source_url for x in listings}

    def test_third_run_starts_over_with_the_longest_ago(self, get):
        listings = [_listing() for _ in range(3)]

        check_listing_validity(batch_size=2)  # listings 0 and 1
        check_listing_validity(batch_size=2)  # listing 2, then the oldest check: listing 0

        assert get.urls[2:] == [listings[2].source_url, listings[0].source_url]

    def test_never_checked_come_before_checked(self, get):
        checked = _listing()
        Listing.objects.filter(pk=checked.pk).update(last_checked_at=timezone.now())
        fresh = _listing()

        check_listing_validity(batch_size=1)

        assert get.urls == [fresh.source_url]

    def test_respects_batch_size(self, get):
        for _ in range(5):
            _listing()

        result = check_listing_validity(batch_size=3)

        assert result["checked"] == 3
        assert len(get.urls) == 3

    def test_batch_size_is_capped(self, get):
        for _ in range(MAX_VALIDITY_BATCH + 3):
            _listing()

        result = check_listing_validity(batch_size=10_000)

        assert result["checked"] == MAX_VALIDITY_BATCH


@pytest.mark.django_db
class TestBlockedSources:
    @pytest.mark.parametrize(
        ("status", "body"),
        [(403, "Forbidden"), (429, "Too many requests"), (401, ""), (200, PERIMETERX_PAGE)],
    )
    def test_block_is_not_treated_as_valid(self, get, status, body):
        listing = _listing()
        get.answers[listing.source_url] = _reply(status, body)

        result = check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.last_check_result == Listing.CheckResult.BLOCKED
        assert listing.is_active is True  # not deactivated either: nothing was learned
        assert result["blocked"] == 1

    def test_block_page_with_stale_wording_does_not_deactivate(self, get):
        listing = _listing()
        page = PERIMETERX_PAGE.replace("</body>", "skelbimas nerastas</body>")
        get.answers[listing.source_url] = _reply(200, page)

        check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.is_active is True
        assert listing.last_check_result == Listing.CheckResult.BLOCKED

    def test_redirect_to_login_page_is_blocked(self, get):
        listing = _listing()
        get.answers[listing.source_url] = _reply(200, "<html></html>", "https://x.invalid/login")

        check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.last_check_result == Listing.CheckResult.BLOCKED

    def test_source_with_blocked_latest_scan_is_skipped(self, get):
        blocked = _listing("alio")
        other = _listing("domoplius")
        baker.make(
            ScrapeRun,
            spider="alio",
            status=ScrapeRun.Status.BLOCKED,
            started_at=timezone.now() - timedelta(hours=5),
        )

        result = check_listing_validity(batch_size=10)

        assert get.urls == [other.source_url]
        assert result["skipped_sources"]["alio"] == "latest scan blocked"
        blocked.refresh_from_db()
        assert blocked.last_checked_at is None  # untouched, not "verified"

    def test_a_later_good_scan_lifts_the_skip(self, get):
        listing = _listing("alio")
        now = timezone.now()
        baker.make(
            ScrapeRun,
            spider="alio",
            status=ScrapeRun.Status.BLOCKED,
            started_at=now - timedelta(days=2),
        )
        baker.make(
            ScrapeRun, spider="alio", status=ScrapeRun.Status.OK, started_at=now - timedelta(days=1)
        )

        result = check_listing_validity(batch_size=10)

        assert get.urls == [listing.source_url]
        assert "alio" not in result["skipped_sources"]

    def test_old_blocked_scan_does_not_count(self, get):
        listing = _listing("alio")
        baker.make(
            ScrapeRun,
            spider="alio",
            status=ScrapeRun.Status.BLOCKED,
            started_at=timezone.now() - timedelta(days=30),
        )

        check_listing_validity(batch_size=10)

        assert get.urls == [listing.source_url]

    def test_running_scan_is_ignored(self, get):
        listing = _listing("alio")
        baker.make(
            ScrapeRun,
            spider="alio",
            status=ScrapeRun.Status.RUNNING,
            started_at=timezone.now() - timedelta(minutes=2),
        )

        check_listing_validity(batch_size=10)

        assert get.urls == [listing.source_url]

    @pytest.mark.parametrize(
        ("spider", "reason"), [("rasknt", "needs login"), ("aruodas", "needs a browser")]
    )
    def test_sources_that_cannot_be_checked_here_are_skipped(self, get, spider, reason):
        _listing(spider)

        result = check_listing_validity(batch_size=10)

        assert get.urls == []
        assert result["checked"] == 0
        assert result["skipped_sources"][spider] == reason

    def test_run_leaves_a_source_alone_after_two_blocks(self, get):
        blocked = [_listing("alio") for _ in range(4)]
        other = _listing("domoplius")
        for listing in blocked:
            get.answers[listing.source_url] = _reply(403, "Forbidden")

        result = check_listing_validity(batch_size=10)

        asked_alio = [u for u in get.urls if "alio" in u]
        assert len(asked_alio) == 2
        assert other.source_url in get.urls
        assert result["blocked"] == 2
        assert result["skipped_sources"]["alio"] == "blocked during this run"
        assert Listing.objects.filter(source="alio", last_checked_at__isnull=True).count() == 2


@pytest.mark.django_db
class TestPoliteness:
    def test_requests_to_one_source_are_at_least_three_seconds_apart(self, get, clock):
        for _ in range(3):
            _listing("domoplius")
        stamps = []
        original = get.side_effect

        def stamped(url, **kwargs):
            stamps.append(clock.now)
            return original(url, **kwargs)

        get.side_effect = stamped

        check_listing_validity(batch_size=10)

        assert len(stamps) == 3
        assert all(b - a >= 3 for a, b in zip(stamps, stamps[1:], strict=False))
        assert clock.sleeps == [3, 3]

    def test_slow_source_keeps_its_registry_delay(self, get, clock):
        _listing("pamario-nt")  # robots.txt Crawl-delay 10
        _listing("pamario-nt")

        check_listing_validity(batch_size=10)

        assert clock.sleeps == [10]

    def test_different_sources_do_not_wait_for_each_other(self, get, clock):
        _listing("domoplius")
        _listing("realu")

        check_listing_validity(batch_size=10)

        assert clock.sleeps == []

    def test_unregistered_source_still_gets_the_three_second_floor(self, get, clock):
        _listing("long-gone-portal")
        _listing("long-gone-portal")

        check_listing_validity(batch_size=10)

        assert clock.sleeps == [3]


@pytest.mark.django_db
class TestFetchModes:
    @pytest.fixture
    def curl(self, monkeypatch):
        """A fake curl_cffi package whose get() answers 200 and records its calls."""
        package = types.ModuleType("curl_cffi")
        requests_mod = types.ModuleType("curl_cffi.requests")
        exceptions_mod = types.ModuleType("curl_cffi.requests.exceptions")

        class CurlError(Exception):
            pass

        exceptions_mod.RequestException = CurlError
        requests_mod.exceptions = exceptions_mod
        requests_mod.get = MagicMock(side_effect=lambda url, **kw: _reply(200, NORMAL_PAGE, url))
        package.requests = requests_mod
        monkeypatch.setitem(sys.modules, "curl_cffi", package)
        monkeypatch.setitem(sys.modules, "curl_cffi.requests", requests_mod)
        monkeypatch.setitem(sys.modules, "curl_cffi.requests.exceptions", exceptions_mod)
        requests_mod.CurlError = CurlError
        return requests_mod

    def test_curl_cffi_sources_use_the_browser_fingerprint(self, get, curl):
        wall = _listing("skelbiu")
        plain = _listing("domoplius")

        check_listing_validity(batch_size=10)

        assert [c.args[0] for c in curl.get.call_args_list] == [wall.source_url]
        assert curl.get.call_args.kwargs["impersonate"] == "chrome"
        assert get.urls == [plain.source_url]
        wall.refresh_from_db()
        assert wall.last_check_result == Listing.CheckResult.OK

    def test_curl_cffi_failure_is_an_error_not_a_verdict(self, get, curl):
        listing = _listing("alio")
        curl.get.side_effect = curl.CurlError("tls failed")

        result = check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.is_active is True
        assert listing.last_check_result == Listing.CheckResult.ERROR
        assert result["errors"] == 1

    def test_missing_curl_cffi_falls_back_to_requests(self, get, monkeypatch):
        monkeypatch.setitem(sys.modules, "curl_cffi", None)  # import raises ImportError
        listing = _listing("skelbiu")
        get.answers[listing.source_url] = _reply(403, "Forbidden")

        check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert get.urls == [listing.source_url]
        assert listing.last_check_result == Listing.CheckResult.BLOCKED  # never "valid"


class _FakeJev:
    def __init__(self, probability: float) -> None:
        self.probability = probability
        self.calls: list[dict] = []

    def system_one(self, *, state, questions, model, timeout):
        self.calls.append({"state": state, "questions": questions})
        return {
            "model": model,
            "answers": {"ad_is_gone": {"type": "noul", "noul": self.probability}},
            "usage": {"input_tokens": 10, "output_tokens": 1},
        }


@pytest.mark.django_db
class TestRedirectedPages:
    HOME = "https://domoplius.example.invalid/"

    def _redirected_listing(self, get) -> Listing:
        listing = _listing()
        get.answers[listing.source_url] = _reply(
            200, "<html><title>Skelbimai</title><body>Visi skelbimai</body></html>", self.HOME
        )
        return listing

    def test_jev_off_never_deactivates_on_a_redirect(self, get):
        listing = self._redirected_listing(get)

        result = check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.is_active is True
        assert listing.last_check_result == Listing.CheckResult.OK
        assert result["deactivated"] == 0
        assert JudgmentLog.objects.count() == 0

    def test_jev_on_deactivates_when_it_says_the_ad_is_gone(self, get, settings, monkeypatch):
        settings.JEV_MODE = "on"
        settings.JEV_MODEL = "jev-1.14.0"
        fake = _FakeJev(0.99)
        monkeypatch.setattr(judge, "get_default_client", lambda: fake)
        listing = self._redirected_listing(get)

        result = check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.is_active is False
        assert listing.last_check_result == Listing.CheckResult.STALE
        assert result["deactivated"] == 1
        state = fake.calls[0]["state"]
        assert state["final_url"] == self.HOME
        assert "Visi skelbimai" in state["text"]

    def test_jev_on_keeps_the_ad_when_it_says_no(self, get, settings, monkeypatch):
        settings.JEV_MODE = "on"
        settings.JEV_MODEL = "jev-1.14.0"
        monkeypatch.setattr(judge, "get_default_client", lambda: _FakeJev(0.01))
        listing = self._redirected_listing(get)

        check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.is_active is True

    def test_unmoved_page_is_not_asked_ad_is_gone(self, get, settings, monkeypatch):
        # An unmoved 200 page gets #18's page_expired question, never the redirect question.
        settings.JEV_MODE = "on"
        settings.JEV_MODEL = "jev-1.14.0"
        fake = _FakeJev(0.99)
        monkeypatch.setattr(judge, "get_default_client", lambda: fake)
        _listing()

        check_listing_validity(batch_size=10)

        assert all("ad_is_gone" not in call["questions"] for call in fake.calls)

    def test_trailing_slash_and_query_are_not_a_move(self, get, settings, monkeypatch):
        settings.JEV_MODE = "on"
        settings.JEV_MODEL = "jev-1.14.0"
        fake = _FakeJev(0.99)
        monkeypatch.setattr(judge, "get_default_client", lambda: fake)
        listing = _listing()
        get.answers[listing.source_url] = _reply(200, NORMAL_PAGE, listing.source_url + "/?utm=1")

        check_listing_validity(batch_size=10)

        assert all("ad_is_gone" not in call["questions"] for call in fake.calls)


@pytest.mark.django_db
class TestCheckValidityAPI:
    def test_check_validity_endpoint(self, get):
        from django.test import Client

        _listing()

        resp = Client().post("/api/check-validity")

        assert resp.status_code == 200
        data = resp.json()
        assert data["checked"] == 1
        assert data["deactivated"] == 0
