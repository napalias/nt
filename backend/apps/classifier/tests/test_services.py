from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.gis.geos import Point
from model_bakery import baker

from apps.classifier import llm
from apps.classifier.models import LearnedPreference, ListingEvaluation, UserFeedback
from apps.classifier.services import (
    _build_system_prompt,
    _listing_to_text,
    classify_batch,
    classify_listing,
    cleanup_batch,
    cleanup_description,
    extract_preferences,
    process_feedback,
)
from apps.classifier.tests.helpers import (
    CLASSIFY_PAYLOAD,
    PREFERENCES_PAYLOAD,
    make_client,
    make_response,
)
from apps.listings.models import Listing

KRETINGA = Point(21.2420, 55.8835, srid=4326)


def _listing(**kwargs):
    kwargs.setdefault("location", KRETINGA)
    kwargs.setdefault("address_raw", "Test")
    return baker.make(Listing, **kwargs)


@pytest.mark.django_db
class TestListingToText:
    def test_basic_listing_text(self):
        listing = baker.make(
            Listing,
            title="Namas Kretingoje",
            source="domoplius",
            source_url="https://domoplius.lt/1",
            property_type=Listing.PropertyType.HOUSE,
            listing_type=Listing.ListingType.SALE,
            price=Decimal("200000"),
            area_sqm=Decimal("100"),
            rooms=4,
            is_new_construction=True,
            address_raw="Kretinga, Kretingos r.",
            city="Kretinga",
            location=Point(21.2420, 55.8835, srid=4326),
        )
        text = _listing_to_text(listing)
        assert "Namas Kretingoje" in text
        assert "200000" in text
        assert "100" in text
        assert "Kretinga" in text
        assert "Taip" in text


@pytest.mark.django_db
class TestListingToTextEdgeCases:
    def test_minimal_listing(self):
        listing = baker.make(
            Listing,
            title="Bare listing",
            source="test",
            source_url="https://test.lt/1",
            property_type=Listing.PropertyType.PLOT,
            listing_type=Listing.ListingType.SALE,
            price=None,
            area_sqm=None,
            rooms=None,
            floor=None,
            year_built=None,
            building_type="",
            heating_type="",
            energy_class="",
            description="",
            city="",
            municipality="",
            district="",
            cadastral_number="",
            photo_urls=[],
            location=Point(21.24, 55.88, srid=4326),
            address_raw="Somewhere",
        )
        text = _listing_to_text(listing)
        assert "Bare listing" in text
        assert "not specified" in text
        assert "0 photo(s)" in text


@pytest.mark.django_db
class TestApiKeyValidation:
    def test_missing_api_key_raises(self, settings):
        settings.ANTHROPIC_API_KEY = ""
        from apps.classifier.services import _get_client

        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            _get_client()


@pytest.mark.django_db
class TestBuildSystemPrompt:
    def test_base_prompt_without_preferences(self):
        prompt = _build_system_prompt()
        assert "Kretinga" in prompt
        assert "Learned Preferences" not in prompt

    def test_prompt_includes_preferences(self):
        LearnedPreference.objects.create(
            preference_type="like",
            pattern="Prefers south-facing windows",
            weight=1.0,
        )
        LearnedPreference.objects.create(
            preference_type="dislike",
            pattern="Avoids narrow plots",
            weight=1.5,
        )
        prompt = _build_system_prompt()
        assert "Learned Preferences" in prompt
        assert "south-facing windows" in prompt
        assert "narrow plots" in prompt

    def test_inactive_preferences_excluded(self):
        LearnedPreference.objects.create(
            preference_type="like",
            pattern="Active preference",
            is_active=True,
        )
        LearnedPreference.objects.create(
            preference_type="like",
            pattern="Inactive preference",
            is_active=False,
        )
        prompt = _build_system_prompt()
        assert "Active preference" in prompt
        assert "Inactive preference" not in prompt


@pytest.mark.django_db
class TestClassifyListing:
    @patch("apps.classifier.services._get_client")
    def test_classify_creates_evaluation(self, mock_get_client):
        mock_get_client.return_value = make_client(make_response(CLASSIFY_PAYLOAD))

        listing = baker.make(
            Listing,
            title="Namas Kretingoje",
            price=Decimal("200000"),
            area_sqm=Decimal("100"),
            is_new_construction=True,
            address_raw="Kretinga",
            city="Kretinga",
            location=Point(21.2420, 55.8835, srid=4326),
        )

        evaluation = classify_listing(listing)
        assert evaluation.verdict == "match"
        assert evaluation.match_score == 0.85
        assert evaluation.listing == listing
        assert ListingEvaluation.objects.count() == 1

    @patch("apps.classifier.services._get_client")
    def test_classify_updates_existing(self, mock_get_client):
        mock_get_client.return_value = make_client(make_response(CLASSIFY_PAYLOAD))

        listing = _listing()
        classify_listing(listing)
        classify_listing(listing)
        assert ListingEvaluation.objects.count() == 1

    @patch("apps.classifier.services._get_client")
    def test_model_comes_from_settings_and_no_forced_tool_choice(self, mock_get_client, settings):
        settings.CLAUDE_EVAL_MODEL = "claude-test-eval"
        client = make_client(make_response(CLASSIFY_PAYLOAD))
        mock_get_client.return_value = client

        evaluation = classify_listing(_listing())

        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-test-eval"
        assert evaluation.model_used == "claude-test-eval"
        assert "tool_choice" not in kwargs
        assert "tools" not in kwargs
        schema = kwargs["output_config"]["format"]["schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) >= {"verdict", "match_score", "summary"}

    @patch("apps.classifier.services._get_client")
    def test_truncated_response_is_not_saved(self, mock_get_client):
        mock_get_client.return_value = make_client(
            make_response(CLASSIFY_PAYLOAD, stop_reason="max_tokens")
        )
        with pytest.raises(llm.TruncatedOutputError):
            classify_listing(_listing())
        assert ListingEvaluation.objects.count() == 0

    @patch("apps.classifier.services._get_client")
    def test_out_of_range_score_is_rejected(self, mock_get_client):
        bad = {**CLASSIFY_PAYLOAD, "match_score": 1.7}
        mock_get_client.return_value = make_client(make_response(bad))
        with pytest.raises(llm.InvalidOutputError):
            classify_listing(_listing())
        assert ListingEvaluation.objects.count() == 0

    @patch("apps.classifier.services._get_client")
    def test_unknown_verdict_is_rejected(self, mock_get_client):
        bad = {**CLASSIFY_PAYLOAD, "verdict": "maybe"}
        mock_get_client.return_value = make_client(make_response(bad))
        with pytest.raises(llm.InvalidOutputError):
            classify_listing(_listing())

    @patch("apps.classifier.services._get_client")
    def test_missing_field_is_rejected(self, mock_get_client):
        bad = {k: v for k, v in CLASSIFY_PAYLOAD.items() if k != "hard_filter_results"}
        mock_get_client.return_value = make_client(make_response(bad))
        with pytest.raises(llm.InvalidOutputError):
            classify_listing(_listing())


@pytest.mark.django_db
class TestClassifyBatch:
    @patch("apps.classifier.services._get_client")
    def test_batch_classifies_unclassified_only(self, mock_get_client):
        mock_get_client.return_value = make_client(make_response(CLASSIFY_PAYLOAD))

        already_done = _listing(is_active=True, address_raw="Done")
        ListingEvaluation.objects.create(
            listing=already_done,
            verdict="match",
            match_score=0.9,
            summary="Existing",
            model_used="test",
        )
        _listing(is_active=True, address_raw="New 1")
        _listing(is_active=True, address_raw="New 2")
        results = classify_batch(limit=10)
        assert len(results.items) == 2
        assert results.failed == 0
        assert ListingEvaluation.objects.count() == 3

    @patch("apps.classifier.services._get_client")
    def test_batch_skips_inactive(self, mock_get_client):
        mock_get_client.return_value = make_client(make_response(CLASSIFY_PAYLOAD))

        _listing(is_active=False, address_raw="Inactive")
        results = classify_batch(limit=10)
        assert len(results.items) == 0

    @patch("apps.classifier.services._get_client")
    def test_batch_respects_limit(self, mock_get_client):
        mock_get_client.return_value = make_client(make_response(CLASSIFY_PAYLOAD))

        for i in range(5):
            _listing(is_active=True, address_raw=f"L{i}")
        results = classify_batch(limit=2)
        assert len(results.items) == 2

    @patch("apps.classifier.services._get_client")
    def test_failures_are_counted_logged_and_do_not_stop_the_batch(self, mock_get_client, caplog):
        good = make_response(CLASSIFY_PAYLOAD)
        truncated = make_response(CLASSIFY_PAYLOAD, stop_reason="max_tokens")
        mock_get_client.return_value = make_client(good, truncated, good)
        for i in range(3):
            _listing(is_active=True, address_raw=f"L{i}")

        with caplog.at_level("ERROR"):
            results = classify_batch(limit=10)

        assert len(results.items) == 2
        assert results.failed == 1
        assert results.error_counts == {"TruncatedOutputError": 1}
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert any("TruncatedOutputError" in r.getMessage() for r in errors)
        assert ListingEvaluation.objects.count() == 2

    @patch("apps.classifier.services._get_client")
    def test_every_call_failing_is_visible_not_a_quiet_zero(self, mock_get_client, caplog):
        mock_get_client.return_value = MagicMock()
        mock_get_client.return_value.messages.create.side_effect = RuntimeError("model not found")
        for i in range(2):
            _listing(is_active=True, address_raw=f"L{i}")

        with caplog.at_level("ERROR"):
            results = classify_batch(limit=10)

        assert results.items == []
        assert results.failed == 2
        assert results.error_counts == {"RuntimeError": 2}
        assert any(r.levelname == "ERROR" and "2 failed" in r.getMessage() for r in caplog.records)

    def test_missing_api_key_fails_loudly(self, settings):
        settings.ANTHROPIC_API_KEY = ""
        _listing(is_active=True)
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            classify_batch(limit=10)


@pytest.mark.django_db
class TestProcessFeedback:
    @patch("apps.classifier.services._get_client")
    def test_feedback_creates_preferences(self, mock_get_client):
        mock_get_client.return_value = make_client(make_response(PREFERENCES_PAYLOAD))

        listing = _listing(title="Test namas")
        feedback, preferences = process_feedback(
            listing, "like", "Great open-plan layout with large windows"
        )
        assert feedback.feedback_type == "like"
        assert len(preferences) == 2
        assert preferences[0].pattern == "Prefers open-plan layouts"
        assert preferences[0].weight == 1.2
        assert LearnedPreference.objects.count() == 2

    @patch("apps.classifier.services._get_client")
    def test_feedback_deletes_old_evaluation(self, mock_get_client):
        mock_get_client.return_value = make_client(make_response(PREFERENCES_PAYLOAD))

        listing = _listing()
        ListingEvaluation.objects.create(
            listing=listing,
            verdict="match",
            match_score=0.9,
            summary="Old",
            model_used="test",
        )
        process_feedback(listing, "dislike", "Actually too noisy")
        assert not ListingEvaluation.objects.filter(listing=listing).exists()


@pytest.mark.django_db
class TestExtractPreferences:
    @patch("apps.classifier.services._get_client")
    def test_uses_fast_model_and_structured_output(self, mock_get_client, settings):
        settings.CLAUDE_FAST_MODEL = "claude-test-fast"
        client = make_client(make_response(PREFERENCES_PAYLOAD))
        mock_get_client.return_value = client
        feedback = UserFeedback.objects.create(
            listing=_listing(), feedback_type="like", reason="open plan"
        )

        extract_preferences(feedback)

        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-test-fast"
        assert "tool_choice" not in kwargs
        assert "format" in kwargs["output_config"]

    @patch("apps.classifier.services._get_client")
    def test_weight_is_clamped_and_patterns_capped(self, mock_get_client):
        payload = {
            "patterns": [
                {"pattern": "a", "weight": 9.0},
                {"pattern": "b", "weight": 0.0},
                {"pattern": "c", "weight": 1.0},
                {"pattern": "d", "weight": 1.0},
            ]
        }
        mock_get_client.return_value = make_client(make_response(payload))
        feedback = UserFeedback.objects.create(listing=_listing(), feedback_type="like", reason="x")

        prefs = extract_preferences(feedback)

        assert [p.pattern for p in prefs] == ["a", "b", "c"]
        assert [p.weight for p in prefs] == [2.0, 0.5, 1.0]

    @patch("apps.classifier.services._get_client")
    def test_truncated_response_saves_nothing(self, mock_get_client):
        mock_get_client.return_value = make_client(
            make_response(PREFERENCES_PAYLOAD, stop_reason="max_tokens")
        )
        feedback = UserFeedback.objects.create(listing=_listing(), feedback_type="like", reason="x")
        with pytest.raises(llm.TruncatedOutputError):
            extract_preferences(feedback)
        assert LearnedPreference.objects.count() == 0


LONG_DESC = "SKUBIAI!!! Puiki investicija, nepraleiskite progos. Namas 100 kv.m, 10 arų sklypas."


@pytest.mark.django_db
class TestCleanupDescription:
    @patch("apps.classifier.services._get_client")
    def test_saves_cleaned_text_using_fast_model(self, mock_get_client, settings):
        settings.CLAUDE_FAST_MODEL = "claude-test-fast"
        client = make_client(make_response(text="Namas 100 kv.m, 10 arų sklypas."))
        mock_get_client.return_value = client
        listing = _listing(description=LONG_DESC)

        cleaned = cleanup_description(listing)

        assert cleaned == "Namas 100 kv.m, 10 arų sklypas."
        listing.refresh_from_db()
        assert listing.description == cleaned
        assert client.messages.create.call_args.kwargs["model"] == "claude-test-fast"
        assert "tool_choice" not in client.messages.create.call_args.kwargs

    @patch("apps.classifier.services._get_client")
    def test_truncated_text_does_not_overwrite_description(self, mock_get_client):
        mock_get_client.return_value = make_client(
            make_response(text="Namas 100 kv.m, 10 ar", stop_reason="max_tokens")
        )
        listing = _listing(description=LONG_DESC)

        with pytest.raises(llm.TruncatedOutputError):
            cleanup_description(listing)

        listing.refresh_from_db()
        assert listing.description == LONG_DESC

    @patch("apps.classifier.services._get_client")
    def test_short_description_makes_no_call(self, mock_get_client):
        listing = _listing(description="short")
        assert cleanup_description(listing) == "short"
        mock_get_client.assert_not_called()

    def test_missing_api_key_fails_loudly(self, settings):
        settings.ANTHROPIC_API_KEY = ""
        listing = _listing(description=LONG_DESC)
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            cleanup_description(listing)

    @patch("apps.classifier.services._get_client")
    def test_batch_counts_failures(self, mock_get_client, caplog):
        mock_get_client.return_value = make_client(
            make_response(text="Namas."),
            make_response(text="cut", stop_reason="max_tokens"),
        )
        _listing(is_active=True, description=LONG_DESC)
        _listing(is_active=True, description=LONG_DESC)

        with caplog.at_level("ERROR"):
            result = cleanup_batch(limit=10)

        assert len(result.items) == 1
        assert result.failed == 1
        assert result.error_counts == {"TruncatedOutputError": 1}
        assert any(r.levelname == "ERROR" for r in caplog.records)
