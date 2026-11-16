"""Jev's scraping screens (#18): house ad, new build, wall kind, expired page, needs cleanup.

No network and no key: Jev is a fake client, Claude is a fake client. Every phone, e-mail and
name here is a placeholder.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.gis.geos import Point
from model_bakery import baker

from apps.classifier import ingest, judge
from apps.classifier.models import JudgmentLog
from apps.classifier.services import cleanup_batch
from apps.classifier.tests.helpers import make_client, make_response
from apps.listings.blocks import BlockKind
from apps.listings.models import Listing

HOUSE = ingest.HOUSE_SALE_AD.name
NEW_BUILD = ingest.IS_NEW_BUILD.name
WALL = ingest.WALL_KIND.name
EXPIRED = ingest.PAGE_EXPIRED.name
CLEANUP = ingest.NEEDS_CLEANUP.name


class FakeJev:
    """Stands in for the TypeSafe client. ``answers`` maps a question name to P(yes) for a
    yes/no question, or to the picked option for a choice (probability ``choice_p``)."""

    def __init__(self, answers, choice_p=0.97):
        self.answers = answers
        self.choice_p = choice_p
        self.calls = []

    def system_one(self, *, state, questions, model, timeout):
        self.calls.append({"state": state, "questions": questions})
        out = {}
        for name, body in questions.items():
            value = self.answers[name]
            if body["type"] == "noul":
                out[name] = {"type": "noul", "noul": value}
                continue
            options = list(body["criteria"])
            rest = (1 - self.choice_p) / max(1, len(options) - 1)
            out[name] = {
                "type": "choice",
                "choice": value,
                "probabilities": {o: self.choice_p if o == value else rest for o in options},
                "confidence": 0.9,
            }
        return {"model": model, "answers": out, "usage": {"input_tokens": 5, "output_tokens": 1}}


@pytest.fixture
def mode(settings, monkeypatch):
    """Switch JEV_MODE and install a fake Jev; returns the fake."""
    settings.JEV_MODEL = "jev-1.14.0"
    settings.JEV_DEFAULT_THRESHOLD = {"yes": 0.90, "no": 0.10, "min_probability": 0.90}
    settings.JEV_THRESHOLDS = {}

    def install(jev_mode, answers=None, **kwargs):
        settings.JEV_MODE = jev_mode
        fake = FakeJev(answers or {}, **kwargs)
        monkeypatch.setattr(judge, "get_default_client", lambda: fake)
        return fake

    return install


def item(**overrides):
    base = {
        "url": "https://www.skelbiu.lt/skelbimai/namas-1.html",
        "source": "skelbiu",
        "source_id": "1",
        "title": "Parduodamas namas Kretingoje",
        "description": "Namas 100 kv.m. Skambinkite +370 600 00000",
        "address": "Kretinga",
        "city": "Kretinga",
        "property_type": "house",
        "listing_type": "sale",
        "price": 200000,
        "year_built": 1990,
        "is_new_construction": False,
        "raw_data": {"phone": "+370 600 00000"},
    }
    return {**base, **overrides}


def rows(question):
    return list(JudgmentLog.objects.filter(question=question).order_by("id"))


@pytest.mark.django_db
class TestOffMode:
    def test_screen_does_not_call_jev_or_log(self, mode):
        fake = mode("off")
        verdict = ingest.screen_listing(item(), mixed_feed=True, house_heuristic=True)
        assert verdict == ingest.ScreenVerdict()
        assert fake.calls == []
        assert JudgmentLog.objects.count() == 0

    def test_a_rejecting_code_filter_still_drops(self, mode):
        mode("off")
        verdict = ingest.screen_listing(item(), mixed_feed=True, house_heuristic=False)
        assert verdict.drop_reason == "Not a house for sale (keyword filter)"

    def test_unmixed_feed_ignores_the_heuristic(self, mode):
        mode("off")
        verdict = ingest.screen_listing(item(), mixed_feed=False, house_heuristic=False)
        assert verdict.drop_reason == ""

    def test_wall_expired_and_cleanup_use_their_old_rules(self, mode):
        fake = mode("off")
        assert ingest.judge_wall("https://x.lt/a", "<html>kitas</html>") is None
        assert ingest.page_expired(1, "<p>Skelbimas nerastas</p>", ["skelbimas nerastas"])
        assert not ingest.page_expired(1, "<p>Namas</p>", ["skelbimas nerastas"])
        assert fake.calls == []
        assert JudgmentLog.objects.count() == 0


@pytest.mark.django_db
class TestHouseAdAndNewBuild:
    def test_shadow_logs_both_answers_and_changes_nothing(self, mode):
        # The code filter rejected the ad; Jev thinks it is a house. Shadow: the filter's
        # verdict stands (the ad is dropped, as off would have), Jev's answer is logged.
        mode("shadow", {HOUSE: 0.97, NEW_BUILD: 0.97})
        verdict = ingest.screen_listing(item(), mixed_feed=True, house_heuristic=False)

        assert verdict.drop_reason == "Not a house for sale (keyword filter)"
        assert verdict.is_new_construction is None  # a shadow verdict never edits the item
        house = rows(HOUSE)[0]
        assert (house.kind, house.mode, house.decided_by) == ("ingest", "shadow", "fallback")
        assert house.jev_answer is True and house.fallback_answer is False
        assert house.agreement is False
        assert rows(NEW_BUILD)[0].agreement is False  # keywords say no (1990), Jev says yes

    def test_shadow_keeps_an_ad_the_filter_accepted_even_if_jev_says_no(self, mode):
        mode("shadow", {HOUSE: 0.02, NEW_BUILD: 0.02})
        verdict = ingest.screen_listing(item(), mixed_feed=True, house_heuristic=True)
        assert verdict.drop_reason == ""
        assert rows(HOUSE)[0].agreement is False

    def test_on_drops_what_jev_says_is_not_a_house(self, mode):
        mode("on", {HOUSE: 0.02, NEW_BUILD: 0.5})
        verdict = ingest.screen_listing(item(), mixed_feed=True, house_heuristic=True)
        assert verdict.drop_reason == "Not a house for sale (Jev)"

    def test_on_keeps_what_jev_says_is_a_house_though_the_filter_said_no(self, mode):
        mode("on", {HOUSE: 0.97, NEW_BUILD: 0.5})
        verdict = ingest.screen_listing(item(), mixed_feed=True, house_heuristic=False)
        assert verdict.drop_reason == ""

    def test_on_unsure_jev_leaves_it_to_the_code_filter(self, mode):
        mode("on", {HOUSE: 0.5, NEW_BUILD: 0.5})
        dropped = ingest.screen_listing(item(), mixed_feed=True, house_heuristic=False)
        kept = ingest.screen_listing(item(), mixed_feed=True, house_heuristic=True)
        assert dropped.drop_reason == "Not a house for sale (keyword filter)"
        assert kept.drop_reason == ""

    def test_on_new_build_comes_from_jev(self, mode):
        mode("on", {HOUSE: 0.97, NEW_BUILD: 0.97})
        verdict = ingest.screen_listing(item(), mixed_feed=True, house_heuristic=True)
        assert verdict.is_new_construction is True

    def test_on_jev_overrules_the_portals_new_build_flag(self, mode):
        mode("on", {HOUSE: 0.97, NEW_BUILD: 0.03})
        flagged = item(is_new_construction=True)  # the portal says new; Jev says old
        verdict = ingest.screen_listing(flagged, mixed_feed=True, house_heuristic=True)
        assert verdict.is_new_construction is False

    def test_on_unsure_new_build_uses_the_keyword_list(self, mode):
        mode("on", {NEW_BUILD: 0.5})
        new = item(title="Naujos statybos namas", year_built="2025", is_new_construction=False)
        old = item(year_built="1980")
        silent = item(year_built=None)
        assert ingest.screen_listing(new, mixed_feed=False, house_heuristic=None) == (
            ingest.ScreenVerdict(is_new_construction=True)
        )
        assert ingest.screen_listing(old, mixed_feed=False, house_heuristic=None) == (
            ingest.ScreenVerdict(is_new_construction=False)
        )
        # keywords abstain: the item keeps its own flag
        assert ingest.screen_listing(silent, mixed_feed=False, house_heuristic=None) == (
            ingest.ScreenVerdict()
        )

    def test_unmixed_feed_asks_only_about_new_build(self, mode):
        fake = mode("shadow", {NEW_BUILD: 0.5})
        ingest.screen_listing(item(source="domoplius"), mixed_feed=False, house_heuristic=None)
        assert list(fake.calls[0]["questions"]) == [NEW_BUILD]

    def test_missing_key_falls_back_without_raising(self, settings):
        settings.JEV_MODE = "on"  # no TYPESAFE_API_KEY: conftest removed it
        verdict = ingest.screen_listing(item(), mixed_feed=True, house_heuristic=False)
        assert verdict.drop_reason == "Not a house for sale (keyword filter)"
        assert "TYPESAFE_API_KEY" in rows(HOUSE)[0].jev_error

    def test_contact_data_and_urls_never_reach_jev(self, mode):
        fake = mode("shadow", {HOUSE: 0.9, NEW_BUILD: 0.5})
        ingest.screen_listing(item(), mixed_feed=True, house_heuristic=True)
        sent = str(fake.calls[0]["state"])
        assert "600 00000" not in sent
        assert "https://" not in sent and "raw_data" not in sent
        assert "Kretinga" in sent  # the address went out under address_raw

    def test_subject_without_a_source_id_is_a_digest_of_the_url(self, mode):
        mode("shadow", {HOUSE: 0.9, NEW_BUILD: 0.5})
        ingest.screen_listing(item(source_id=""), mixed_feed=True, house_heuristic=True)
        subject = rows(HOUSE)[0].subject_id
        assert subject.startswith("skelbiu:") and len(subject) <= 64


@pytest.mark.django_db
class TestWallKind:
    SHORT_BLOCK = "<html><head><title>Tikriname</title></head><body>Palaukite...</body></html>"

    def test_looks_ambiguous_only_for_pages_with_little_to_read(self):
        assert ingest.looks_ambiguous(self.SHORT_BLOCK)
        assert ingest.looks_ambiguous("<script>var a=1;" + "x" * 5000 + "</script><p>Hi</p>")
        assert not ingest.looks_ambiguous("<p>" + "namas " * 300 + "</p>")

    def test_on_returns_the_kind_jev_picks(self, mode):
        mode("on", {WALL: "challenge"})
        kind = ingest.judge_wall("https://www.skelbiu.lt/paieska/?q=1", self.SHORT_BLOCK)
        assert kind is BlockKind.CHALLENGE
        row = rows(WALL)[0]
        assert row.kind == "wall" and row.decided_by == "jev" and row.jev_answer == "challenge"

    def test_on_none_means_no_wall(self, mode):
        mode("on", {WALL: "none"})
        assert ingest.judge_wall("https://x.lt/a", self.SHORT_BLOCK) is None

    def test_shadow_logs_jev_against_the_codes_none_and_returns_no_wall(self, mode):
        mode("shadow", {WALL: "captcha"})
        assert ingest.judge_wall("https://x.lt/a", self.SHORT_BLOCK) is None
        row = rows(WALL)[0]
        assert row.mode == "shadow" and row.decided_by == "fallback"
        assert row.jev_answer == "captcha" and row.fallback_answer == "none"
        assert row.agreement is False

    def test_the_page_text_is_sent_without_scripts(self, mode):
        fake = mode("shadow", {WALL: "none"})
        page = "<title>T</title><script>secret()</script><p>Sveiki</p>"
        ingest.judge_wall("https://x.lt/a?token=abc", page)
        state = fake.calls[0]["state"]
        assert state["text"] == "T Sveiki"
        assert state["title"] == "T" and state["url_path"] == "/a"
        assert "token" not in str(state)

    def test_the_options_are_none_and_every_block_kind_a_page_can_show(self):
        # robots.txt is a refusal before any page, so a page never shows it.
        page_kinds = {k.value for k in BlockKind} - {BlockKind.ROBOTS.value}
        assert set(ingest.WALL_KINDS) == {"none"} | page_kinds


@pytest.mark.django_db
class TestExpiredPage:
    MARKERS = ["nebepasiekiamas", "skelbimas nerastas"]
    GONE = "<html><title>Skelbimas</title><h1>Skelbimas nebepasiekiamas</h1></html>"
    LIVE = "<html><title>Namas</title><h1>Parduodamas namas</h1><p>Geras namas</p></html>"

    def test_shadow_marker_decides_and_jev_is_logged(self, mode):
        mode("shadow", {EXPIRED: 0.97})
        assert ingest.page_expired(7, self.LIVE, self.MARKERS) is False
        row = rows(EXPIRED)[0]
        assert (row.kind, row.subject_id, row.mode) == ("expired", "7", "shadow")
        assert row.jev_answer is True and row.fallback_answer is False and not row.agreement

    def test_shadow_marker_hit_stays_expired(self, mode):
        mode("shadow", {EXPIRED: 0.03})
        assert ingest.page_expired(7, self.GONE, self.MARKERS) is True

    def test_on_jev_catches_a_page_the_markers_miss(self, mode):
        mode("on", {EXPIRED: 0.97})
        assert ingest.page_expired(7, self.LIVE, self.MARKERS) is True

    def test_on_jev_overrules_a_marker_in_a_live_page(self, mode):
        mode("on", {EXPIRED: 0.02})
        footer = self.LIVE + "<footer>Senas skelbimas nebepasiekiamas po 30 d.</footer>"
        assert ingest.page_expired(7, footer, self.MARKERS) is False

    def test_on_unsure_jev_leaves_it_to_the_markers(self, mode):
        mode("on", {EXPIRED: 0.5})
        assert ingest.page_expired(7, self.GONE, self.MARKERS) is True
        assert ingest.page_expired(7, self.LIVE, self.MARKERS) is False

    def test_the_heading_and_title_go_to_jev(self, mode):
        fake = mode("shadow", {EXPIRED: 0.5})
        ingest.page_expired(7, self.GONE, self.MARKERS)
        state = fake.calls[0]["state"]
        assert state["heading"] == "Skelbimas nebepasiekiamas" and state["title"] == "Skelbimas"


LONG_FLUFF = "SKUBIAI!!! Puiki investicija, nepraleiskite progos. Namas 100 kv.m, 10 arų sklypas."
LONG_PLAIN = "Namas 100 kv.m, 10 arų sklypas, keturi kambariai, šildymas dujomis, garažas."


def make_listing(description, **kwargs):
    kwargs.setdefault("is_active", True)
    kwargs.setdefault("property_type", Listing.PropertyType.HOUSE)
    kwargs.setdefault("content_hash", "h" * 64)
    kwargs.setdefault("location", Point(21.2420, 55.8835, srid=4326))
    kwargs.setdefault("address_raw", "Test")
    return baker.make(
        Listing,
        description=description,
        description_raw=description,
        description_clean="",
        **kwargs,
    )


@pytest.mark.django_db
class TestNeedsCleanup:
    def test_off_candidates_are_exactly_the_old_regex_query(self, mode):
        mode("off")
        flagged = make_listing(LONG_FLUFF)
        make_listing(LONG_PLAIN)
        assert ingest.cleanup_candidates(10) == [flagged]

    def test_shadow_adds_a_few_unflagged_listings_and_skips_judged_ones(self, mode, settings):
        mode("shadow", {CLEANUP: 0.5})
        flagged = make_listing(LONG_FLUFF)
        plain = [make_listing(LONG_PLAIN, content_hash=f"{i:064d}") for i in range(3)]
        short = make_listing("kort")
        inactive = make_listing(LONG_PLAIN, is_active=False)
        with patch.object(ingest, "SHADOW_EXTRA_PER_RUN", 2):
            first = ingest.cleanup_candidates(10)
            assert first == [flagged, plain[0], plain[1]]
            assert short not in first and inactive not in first
            ingest.needs_cleanup(plain[0])  # judged: logged under its content hash
            assert ingest.cleanup_candidates(10) == [flagged, plain[1], plain[2]]
        # A listing that changed (new content hash) is judged again.
        Listing.objects.filter(pk=plain[0].pk).update(content_hash="c" * 64)
        with patch.object(ingest, "SHADOW_EXTRA_PER_RUN", 5):
            assert plain[0] in ingest.cleanup_candidates(10)

    def test_shadow_regex_decides_and_jev_is_logged(self, mode):
        mode("shadow", {CLEANUP: 0.97})
        plain = make_listing(LONG_PLAIN)
        assert ingest.needs_cleanup(plain) is False
        row = rows(CLEANUP)[0]
        assert row.kind == "cleanup" and row.jev_answer is True and row.fallback_answer is False

    def test_on_candidates_cover_every_unflagged_listing_up_to_the_limit(self, mode):
        mode("on", {CLEANUP: 0.5})
        flagged = make_listing(LONG_FLUFF)
        plain = [make_listing(LONG_PLAIN, content_hash=f"{i:064d}") for i in range(4)]
        assert ingest.cleanup_candidates(3) == [flagged, *plain[:3]]


@pytest.mark.django_db
class TestCleanupBatch:
    @staticmethod
    def run(settings):
        """cleanup_batch with a fake Claude client; returns (result, client)."""
        settings.CLAUDE_FAST_MODEL = "claude-test-fast"
        client = make_client(make_response(text="Namas 100 kv.m."))
        with patch("apps.classifier.services._get_client", return_value=client):
            return cleanup_batch(limit=10), client

    def test_off_cleans_what_the_regex_flags_and_asks_nobody(self, mode, settings):
        fake = mode("off")
        flagged = make_listing(LONG_FLUFF)
        make_listing(LONG_PLAIN)
        result, client = self.run(settings)
        assert result.items == [flagged.pk]
        assert fake.calls == [] and JudgmentLog.objects.count() == 0
        # The rewrite itself stays on the fast Claude model.
        assert client.messages.create.call_args.kwargs["model"] == "claude-test-fast"

    def test_shadow_cleans_exactly_what_off_would(self, mode, settings):
        mode("shadow", {CLEANUP: 0.97})  # Jev wants everything cleaned; shadow ignores it
        flagged = make_listing(LONG_FLUFF)
        plain = make_listing(LONG_PLAIN)
        result, client = self.run(settings)
        assert result.items == [flagged.pk]
        assert client.messages.create.call_count == 1
        assert {r.subject_id for r in rows(CLEANUP)} == {
            f"{flagged.pk}:{'h' * 16}",
            f"{plain.pk}:{'h' * 16}",
        }
        plain.refresh_from_db()
        assert plain.description_clean == ""

    def test_on_jev_finds_noise_the_regex_missed_and_spares_what_it_flagged(
        self, mode, settings, monkeypatch
    ):
        class ByText(FakeJev):
            """Jev finds the shouting listing fine and the plain one noisy: the opposite of the
            regex, so the test shows who decides."""

            def system_one(self, *, state, questions, model, timeout):
                self.answers = {CLEANUP: 0.02 if "SKUBIAI" in state["description"] else 0.97}
                return super().system_one(
                    state=state, questions=questions, model=model, timeout=timeout
                )

        mode("on")
        monkeypatch.setattr(judge, "get_default_client", lambda: ByText({}))
        flagged = make_listing(LONG_FLUFF)
        plain = make_listing(LONG_PLAIN)
        result, client = self.run(settings)
        assert result.items == [plain.pk]
        plain.refresh_from_db()
        flagged.refresh_from_db()
        assert plain.description_clean == "Namas 100 kv.m."
        assert flagged.description_clean == ""
        assert client.messages.create.call_args.kwargs["model"] == "claude-test-fast"
