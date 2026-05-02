from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.gis.geos import Point
from model_bakery import baker

from apps.classifier.models import LearnedPreference, ListingEvaluation
from apps.classifier.services import (
    _build_system_prompt,
    _listing_to_text,
    classify_batch,
    classify_listing,
    process_feedback,
)
from apps.listings.models import Listing


def _make_mock_classify_response():
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "evaluate_listing"
    tool_block.input = {
        "verdict": "match",
        "match_score": 0.85,
        "summary": "Puikus namas Kretingoje, atitinka visus kriterijus.",
        "hard_filter_results": {
            "price_ok": True,
            "type_ok": True,
            "area_ok": True,
            "plot_ok": True,
            "location_ok": True,
        },
        "quality_notes": ["Good photos", "Complete description"],
        "red_flags": [],
    }
    response = MagicMock()
    response.content = [tool_block]
    return response


def _make_mock_extract_response():
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "extract_preferences"
    tool_block.input = {
        "patterns": [
            {"pattern": "Prefers open-plan layouts", "weight": 1.2},
            {"pattern": "Values large windows facing south", "weight": 1.0},
        ]
    }
    response = MagicMock()
    response.content = [tool_block]
    return response


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
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_mock_classify_response()
        mock_get_client.return_value = mock_client

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
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_mock_classify_response()
        mock_get_client.return_value = mock_client

        listing = baker.make(
            Listing,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Test",
        )
        classify_listing(listing)
        classify_listing(listing)
        assert ListingEvaluation.objects.count() == 1


@pytest.mark.django_db
class TestClassifyBatch:
    @patch("apps.classifier.services._get_client")
    def test_batch_classifies_unclassified_only(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_mock_classify_response()
        mock_get_client.return_value = mock_client

        already_done = baker.make(
            Listing,
            is_active=True,
            location=Point(21.24, 55.88, srid=4326),
            address_raw="Done",
        )
        ListingEvaluation.objects.create(
            listing=already_done,
            verdict="match",
            match_score=0.9,
            summary="Existing",
            model_used="test",
        )
        baker.make(
            Listing,
            is_active=True,
            location=Point(21.24, 55.88, srid=4326),
            address_raw="New 1",
        )
        baker.make(
            Listing,
            is_active=True,
            location=Point(21.24, 55.88, srid=4326),
            address_raw="New 2",
        )
        results = classify_batch(limit=10)
        assert len(results) == 2
        assert ListingEvaluation.objects.count() == 3

    @patch("apps.classifier.services._get_client")
    def test_batch_skips_inactive(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_mock_classify_response()
        mock_get_client.return_value = mock_client

        baker.make(
            Listing,
            is_active=False,
            location=Point(21.24, 55.88, srid=4326),
            address_raw="Inactive",
        )
        results = classify_batch(limit=10)
        assert len(results) == 0

    @patch("apps.classifier.services._get_client")
    def test_batch_respects_limit(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_mock_classify_response()
        mock_get_client.return_value = mock_client

        for i in range(5):
            baker.make(
                Listing,
                is_active=True,
                location=Point(21.24, 55.88, srid=4326),
                address_raw=f"L{i}",
            )
        results = classify_batch(limit=2)
        assert len(results) == 2


@pytest.mark.django_db
class TestProcessFeedback:
    @patch("apps.classifier.services._get_client")
    def test_feedback_creates_preferences(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_mock_extract_response()
        mock_get_client.return_value = mock_client

        listing = baker.make(
            Listing,
            title="Test namas",
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Test",
        )
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
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_mock_extract_response()
        mock_get_client.return_value = mock_client

        listing = baker.make(
            Listing,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Test",
        )
        ListingEvaluation.objects.create(
            listing=listing,
            verdict="match",
            match_score=0.9,
            summary="Old",
            model_used="test",
        )
        process_feedback(listing, "dislike", "Actually too noisy")
        assert not ListingEvaluation.objects.filter(listing=listing).exists()
