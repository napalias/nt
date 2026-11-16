"""The evaluation pipeline: code filters, Jev yes/no judgments, then a Claude narrative.

No network and no key: Claude and Jev are fakes. A listing that hard-fails never reaches Claude.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.gis.geos import Point
from model_bakery import baker

from apps.classifier import judge, llm
from apps.classifier.models import JudgmentLog, ListingEvaluation
from apps.classifier.services import classify_batch, classify_listing
from apps.classifier.tests.helpers import (
    CLASSIFY_PAYLOAD,
    NARRATIVE_PAYLOAD,
    make_client,
    make_response,
)
from apps.classifier.tests.test_judge import FakeJev, envelope, noul
from apps.listings.models import Listing

KRETINGA = Point(21.2424, 55.8901, srid=4326)
KLAIPEDA = Point(21.1443, 55.7033, srid=4326)


def _listing(**kwargs) -> Listing:
    """A listing that passes every filter; override one field to break it."""
    values = {
        "title": "Naujas namas",
        "description": "Naujos statybos namas",
        "property_type": Listing.PropertyType.HOUSE,
        "listing_type": Listing.ListingType.SALE,
        "price": Decimal("200000"),
        "area_sqm": Decimal("100"),
        "plot_area_ares": Decimal("10"),
        "is_new_construction": True,
        "year_built": None,
        "address_raw": "Kretinga",
        "city": "Kretinga",
        "location": KRETINGA,
        "is_active": True,
    }
    values.update(kwargs)
    return baker.make(Listing, **values)


HARD_FAILS = {
    "price": ({"price": Decimal("300000")}, "price_ok"),
    "area": ({"area_sqm": Decimal("80")}, "area_ok"),
    "plot": ({"plot_area_ares": Decimal("20")}, "plot_ok"),
    "location": (
        {"location": KLAIPEDA, "city": "Klaipėda", "address_raw": "Klaipėda"},
        "location_ok",
    ),
    "part_of_house": (
        {"property_type": Listing.PropertyType.PART_OF_HOUSE},
        "type_ok",
    ),
    "part_of_house_wording": ({"description": "Parduodama namo dalis"}, "type_ok"),
    "not_new": (
        {"is_new_construction": False, "year_built": 1975, "title": "Namas", "description": ""},
        "type_ok",
    ),
}


@pytest.mark.django_db
class TestHardFailMakesNoClaudeCall:
    @pytest.mark.parametrize(("overrides", "key"), HARD_FAILS.values(), ids=HARD_FAILS.keys())
    def test_skip_without_a_claude_call(self, overrides, key):
        client = make_client(make_response(NARRATIVE_PAYLOAD))
        listing = _listing(**overrides)

        with (
            patch.object(llm, "call_structured") as call_structured,
            patch.object(llm, "call_text") as call_text,
        ):
            evaluation = classify_listing(listing, client=client)

        client.messages.create.assert_not_called()
        call_structured.assert_not_called()
        call_text.assert_not_called()
        assert evaluation.verdict == "skip"
        assert 0.0 <= evaluation.match_score <= 0.2
        assert evaluation.hard_filter_results[key] is False
        assert evaluation.summary
        assert evaluation.model_used == "code"
        assert ListingEvaluation.objects.get(listing=listing).verdict == "skip"

    @patch("apps.classifier.services._get_client")
    def test_batch_of_hard_fails_calls_nothing(self, mock_get_client):
        client = make_client(make_response(NARRATIVE_PAYLOAD))
        mock_get_client.return_value = client
        _listing(price=Decimal("999999"))
        _listing(area_sqm=Decimal("40"))

        result = classify_batch(limit=10)

        assert [e.verdict for e in result.items] == ["skip", "skip"]
        client.messages.create.assert_not_called()

    def test_jev_no_on_new_build_is_a_hard_fail_too(self, monkeypatch, settings):
        settings.JEV_MODE = "on"
        monkeypatch.setattr(
            judge,
            "_default_client",
            FakeJev(envelope({"is_new_build": noul(0.02), "is_part_of_house": noul(0.01)})),
        )
        client = make_client(make_response(NARRATIVE_PAYLOAD))
        # The flag says new and the keyword path would agree. Jev says it is not new.
        listing = _listing()

        evaluation = classify_listing(listing, client=client)

        client.messages.create.assert_not_called()
        assert evaluation.verdict == "skip"
        assert evaluation.hard_filter_results["type_ok"] is False
        rows = {r.question: r for r in JudgmentLog.objects.filter(kind="evaluation")}
        assert rows["is_new_build"].decided_by == "jev"
        assert rows["is_new_build"].subject_id == str(listing.pk)


@pytest.mark.django_db
class TestNarrativeForListingsThatPass:
    def test_match_gets_one_narrative_call_on_the_eval_model(self, settings):
        settings.CLAUDE_EVAL_MODEL = "claude-test-eval"
        client = make_client(make_response(NARRATIVE_PAYLOAD))

        evaluation = classify_listing(_listing(), client=client)

        assert client.messages.create.call_count == 1
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-test-eval"
        assert evaluation.model_used == "claude-test-eval"
        assert "tool_choice" not in kwargs
        schema = kwargs["output_config"]["format"]["schema"]
        assert set(schema["required"]) == {"summary", "quality_notes", "red_flags"}
        assert "verdict" not in schema["properties"]
        assert evaluation.verdict == "match"
        assert 0.8 <= evaluation.match_score <= 1.0
        assert evaluation.summary == NARRATIVE_PAYLOAD["summary"]
        assert evaluation.quality_notes == NARRATIVE_PAYLOAD["quality_notes"]
        assert evaluation.red_flags == []
        assert evaluation.hard_filter_results == {
            "price_ok": True,
            "type_ok": True,
            "area_ok": True,
            "plot_ok": True,
            "location_ok": True,
        }

    def test_the_prompt_carries_the_code_findings(self):
        client = make_client(make_response(NARRATIVE_PAYLOAD))
        classify_listing(_listing(plot_area_ares=None), client=client)

        kwargs = client.messages.create.call_args.kwargs
        content = kwargs["messages"][0]["content"]
        assert "plot: unknown" in content
        assert "price: pass" in content
        assert "Kretinga" in kwargs["system"][0]["text"]

    def test_claude_cannot_change_the_verdict_or_score(self):
        payload = {**NARRATIVE_PAYLOAD, "verdict": "skip", "match_score": 0.0}
        client = make_client(make_response(payload))

        evaluation = classify_listing(_listing(), client=client)

        assert evaluation.verdict == "match"
        assert evaluation.match_score >= 0.8

    def test_missing_value_is_review_not_skip(self):
        client = make_client(make_response(NARRATIVE_PAYLOAD))

        evaluation = classify_listing(_listing(plot_area_ares=None), client=client)

        assert evaluation.verdict == "review"
        assert 0.4 <= evaluation.match_score < 0.8
        assert "plot_ok" not in evaluation.hard_filter_results
        assert evaluation.quality_notes[0].startswith("Nežinoma")
        assert client.messages.create.call_count == 1

    def test_approximate_coordinates_make_location_unknown_not_fail(self):
        listing = _listing(location=KLAIPEDA, city="", address_raw="Gatvė 1")
        listing.location_precision = "city"  # the field lands with #5; absent today
        client = make_client(make_response(NARRATIVE_PAYLOAD))

        evaluation = classify_listing(listing, client=client)

        assert evaluation.verdict == "review"
        assert "location_ok" not in evaluation.hard_filter_results
        assert client.messages.create.call_count == 1

    def test_unknown_new_build_is_review(self):
        listing = _listing(
            is_new_construction=False, title="Namas", description="Namas", year_built=None
        )
        client = make_client(make_response(NARRATIVE_PAYLOAD))

        evaluation = classify_listing(listing, client=client)

        assert evaluation.verdict == "review"
        assert "type_ok" not in evaluation.hard_filter_results

    def test_truncated_narrative_is_not_saved(self):
        client = make_client(make_response(NARRATIVE_PAYLOAD, stop_reason="max_tokens"))
        with pytest.raises(llm.TruncatedOutputError):
            classify_listing(_listing(), client=client)
        assert ListingEvaluation.objects.count() == 0

    @pytest.mark.parametrize(
        "bad",
        [
            {"quality_notes": [], "red_flags": []},
            {**NARRATIVE_PAYLOAD, "summary": ""},
            {**NARRATIVE_PAYLOAD, "red_flags": "none"},
            {**NARRATIVE_PAYLOAD, "quality_notes": [1]},
        ],
    )
    def test_bad_narrative_is_rejected(self, bad):
        client = make_client(make_response(bad))
        with pytest.raises(llm.InvalidOutputError):
            classify_listing(_listing(), client=client)
        assert ListingEvaluation.objects.count() == 0

    def test_old_evaluation_survives_a_failed_re_evaluation(self):
        listing = _listing()
        ListingEvaluation.objects.create(
            listing=listing, verdict="match", match_score=0.9, summary="Old", model_used="x"
        )
        client = make_client(make_response(NARRATIVE_PAYLOAD, stop_reason="max_tokens"))
        with pytest.raises(llm.TruncatedOutputError):
            classify_listing(listing, client=client)
        assert ListingEvaluation.objects.get(listing=listing).summary == "Old"


@pytest.mark.django_db
class TestJevQuestionsAreAsked:
    def test_both_questions_go_to_jev_in_one_request(self, monkeypatch, settings):
        settings.JEV_MODE = "on"
        fake = FakeJev(envelope({"is_new_build": noul(0.97), "is_part_of_house": noul(0.02)}))
        monkeypatch.setattr(judge, "_default_client", fake)
        client = make_client(make_response(NARRATIVE_PAYLOAD))

        evaluation = classify_listing(
            _listing(is_new_construction=False, title="Namas", description=""), client
        )

        assert len(fake.calls) == 1
        assert set(fake.calls[0]["questions"]) == {"is_new_build", "is_part_of_house"}
        assert evaluation.verdict == "match"

    def test_jev_is_not_asked_once_a_code_filter_has_failed(self, monkeypatch, settings):
        settings.JEV_MODE = "on"
        fake = FakeJev(envelope({"is_new_build": noul(0.97), "is_part_of_house": noul(0.02)}))
        monkeypatch.setattr(judge, "_default_client", fake)

        classify_listing(_listing(price=Decimal("999999")), make_client())

        assert fake.calls == []

    def test_unavailable_jev_falls_back_to_the_keyword_path(self, settings):
        settings.JEV_MODE = "on"  # no key in tests: Jev is unavailable, never raises
        client = make_client(make_response(NARRATIVE_PAYLOAD))

        evaluation = classify_listing(_listing(), client=client)

        assert evaluation.verdict == "match"
        rows = JudgmentLog.objects.filter(kind="evaluation")
        assert {r.decided_by for r in rows} == {"fallback"}
        assert all(r.jev_error for r in rows)

    def test_off_mode_never_logs(self):
        classify_listing(_listing(), client=make_client(make_response(NARRATIVE_PAYLOAD)))
        assert JudgmentLog.objects.count() == 0


@pytest.mark.django_db
class TestShadowMode:
    @pytest.fixture(autouse=True)
    def shadow(self, settings):
        settings.JEV_MODE = "shadow"

    def test_old_full_evaluation_still_runs_and_is_saved(self, settings):
        settings.CLAUDE_EVAL_MODEL = "claude-test-eval"
        client = make_client(make_response(CLASSIFY_PAYLOAD))

        evaluation = classify_listing(_listing(), client=client)

        assert client.messages.create.call_count == 1
        schema = client.messages.create.call_args.kwargs["output_config"]["format"]["schema"]
        assert "verdict" in schema["required"]
        assert evaluation.verdict == "match"
        assert evaluation.match_score == 0.85
        assert evaluation.summary == CLASSIFY_PAYLOAD["summary"]
        assert evaluation.model_used == "claude-test-eval"

    def test_hard_fail_still_gets_the_old_evaluation(self):
        client = make_client(make_response(CLASSIFY_PAYLOAD))
        classify_listing(_listing(price=Decimal("999999")), client=client)
        assert client.messages.create.call_count == 1

    def test_jev_questions_are_logged_in_shadow(self):
        classify_listing(_listing(), client=make_client(make_response(CLASSIFY_PAYLOAD)))
        rows = JudgmentLog.objects.filter(kind="evaluation")
        assert {r.question for r in rows} == {"is_new_build", "is_part_of_house"}
        assert {r.mode for r in rows} == {"shadow"}

    def test_agreement_is_logged_per_filter(self, caplog):
        with caplog.at_level("INFO", logger="apps.classifier.services"):
            classify_listing(_listing(), client=make_client(make_response(CLASSIFY_PAYLOAD)))

        lines = [r.getMessage() for r in caplog.records if "shadow filter" in r.getMessage()]
        for key in ("price_ok", "type_ok", "area_ok", "plot_ok", "location_ok"):
            assert any(f"filter={key} " in line and "agree=True" in line for line in lines), key

    def test_disagreement_with_the_old_evaluation_is_visible(self, caplog):
        old = {
            **CLASSIFY_PAYLOAD,
            "hard_filter_results": {**CLASSIFY_PAYLOAD["hard_filter_results"], "area_ok": True},
        }
        listing = _listing(area_sqm=Decimal("80"))

        with caplog.at_level("INFO", logger="apps.classifier.services"):
            classify_listing(listing, client=make_client(make_response(old)))

        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any(
            "filter=area_ok" in r.getMessage() and "agree=False" in r.getMessage() for r in warnings
        )

    def test_unknown_code_values_are_not_counted_as_disagreement(self, caplog):
        listing = _listing(plot_area_ares=None)
        with caplog.at_level("INFO", logger="apps.classifier.services"):
            classify_listing(listing, client=make_client(make_response(CLASSIFY_PAYLOAD)))

        lines = [r.getMessage() for r in caplog.records if "filter=plot_ok" in r.getMessage()]
        assert lines and "code=unknown" in lines[0] and "agree=None" in lines[0]
