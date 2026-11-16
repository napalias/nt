import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.gis.geos import Point
from django.test import Client
from model_bakery import baker

from apps.classifier.models import LearnedPreference, ListingEvaluation, UserFeedback
from apps.classifier.tests.helpers import NARRATIVE_PAYLOAD, make_response
from apps.listings.models import Listing


def _make_mock_classify_response():
    return make_response({**NARRATIVE_PAYLOAD, "summary": "Puikus namas."})


def _make_mock_extract_response():
    return make_response({"patterns": [{"pattern": "Prefers open layout", "weight": 1.0}]})


@pytest.fixture
def api_client():
    return Client()


@pytest.fixture
def listing():
    return baker.make(
        Listing,
        title="Test namas",
        price=Decimal("200000"),
        area_sqm=Decimal("100"),
        plot_area_ares=Decimal("10"),
        property_type=Listing.PropertyType.HOUSE,
        is_new_construction=True,
        location=Point(21.2420, 55.8835, srid=4326),
        address_raw="Kretinga",
        city="Kretinga",
    )


@pytest.mark.django_db
class TestClassifyEndpoint:
    @patch("apps.classifier.services._get_client")
    def test_classify_single(self, mock_get_client, api_client, listing):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_mock_classify_response()
        mock_get_client.return_value = mock_client

        resp = api_client.post(f"/api/classifier/classify/{listing.pk}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "match"
        assert 0.8 <= data["match_score"] <= 1.0
        assert data["summary"] == "Puikus namas."
        assert data["listing_id"] == listing.pk

    def test_classify_nonexistent_listing(self, api_client):
        resp = api_client.post("/api/classifier/classify/99999")
        assert resp.status_code == 404

    @patch("apps.classifier.services._get_client")
    def test_classify_batch(self, mock_get_client, api_client):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_mock_classify_response()
        mock_get_client.return_value = mock_client

        for i in range(3):
            baker.make(
                Listing,
                is_active=True,
                property_type=Listing.PropertyType.HOUSE,
                location=Point(21.2420, 55.8835, srid=4326),
                address_raw=f"Test {i}",
            )

        resp = api_client.post(
            "/api/classifier/classify/batch",
            data=json.dumps({"limit": 10}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["classified_count"] == 3
        assert data["failed_count"] == 0
        assert data["errors"] == {}

    @patch("apps.classifier.services._get_client")
    def test_classify_batch_reports_failures(self, mock_get_client, api_client):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("model not found")
        mock_get_client.return_value = mock_client
        baker.make(
            Listing,
            is_active=True,
            property_type=Listing.PropertyType.HOUSE,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Test",
        )

        resp = api_client.post(
            "/api/classifier/classify/batch",
            data=json.dumps({"limit": 10}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["classified_count"] == 0
        assert data["failed_count"] == 1
        assert data["errors"] == {"RuntimeError": 1}


@pytest.mark.django_db
class TestFeedbackEndpoint:
    @patch("apps.classifier.services._get_client")
    def test_submit_feedback(self, mock_get_client, api_client, listing):
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_mock_extract_response()
        mock_get_client.return_value = mock_client

        resp = api_client.post(
            f"/api/classifier/feedback/{listing.pk}",
            data=json.dumps(
                {
                    "feedback_type": "like",
                    "reason": "Great layout",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["feedback_type"] == "like"
        assert len(data["extracted_preferences"]) == 1

    def test_feedback_nonexistent_listing(self, api_client):
        resp = api_client.post(
            "/api/classifier/feedback/99999",
            data=json.dumps(
                {
                    "feedback_type": "dislike",
                    "reason": "Test",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_list_feedback(self, api_client, listing):
        UserFeedback.objects.create(listing=listing, feedback_type="like", reason="Nice")
        resp = api_client.get("/api/classifier/feedback")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["listing_title"] == "Test namas"


@pytest.mark.django_db
class TestEvaluatedEndpoint:
    def test_list_evaluated_empty(self, api_client):
        resp = api_client.get("/api/classifier/evaluated")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_evaluated_with_filter(self, api_client, listing):
        ListingEvaluation.objects.create(
            listing=listing,
            verdict="match",
            match_score=0.9,
            summary="Good",
            model_used="test",
        )
        resp = api_client.get("/api/classifier/evaluated?verdict=match")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1

        resp = api_client.get("/api/classifier/evaluated?verdict=skip")
        assert resp.json() == []

    def test_list_evaluated_min_score(self, api_client):
        for score, verdict in [(0.9, "match"), (0.3, "skip")]:
            lst = baker.make(
                Listing,
                location=Point(21.24, 55.88, srid=4326),
                address_raw="T",
            )
            ListingEvaluation.objects.create(
                listing=lst,
                verdict=verdict,
                match_score=score,
                summary="T",
                model_used="t",
            )
        resp = api_client.get("/api/classifier/evaluated?min_score=0.5")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["match_score"] == 0.9


@pytest.mark.django_db
class TestPreferencesEndpoint:
    def test_list_preferences(self, api_client):
        LearnedPreference.objects.create(
            preference_type="like",
            pattern="Test pattern",
            weight=1.0,
        )
        resp = api_client.get("/api/classifier/preferences")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["pattern"] == "Test pattern"

    def test_list_excludes_inactive(self, api_client):
        LearnedPreference.objects.create(
            preference_type="like",
            pattern="Active",
            is_active=True,
        )
        LearnedPreference.objects.create(
            preference_type="like",
            pattern="Inactive",
            is_active=False,
        )
        resp = api_client.get("/api/classifier/preferences")
        data = resp.json()
        assert len(data) == 1

    def test_delete_preference(self, api_client):
        pref = LearnedPreference.objects.create(
            preference_type="like",
            pattern="To delete",
        )
        resp = api_client.delete(f"/api/classifier/preferences/{pref.pk}")
        assert resp.status_code == 200
        pref.refresh_from_db()
        assert pref.is_active is False
