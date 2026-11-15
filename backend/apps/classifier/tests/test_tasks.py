from unittest.mock import MagicMock, patch

import pytest
from django.contrib.gis.geos import Point
from model_bakery import baker

from apps.classifier.models import ListingEvaluation
from apps.classifier.tasks import classify_new_listings, cleanup_descriptions, reclassify_all
from apps.classifier.tests.helpers import NARRATIVE_PAYLOAD, make_client, make_response
from apps.listings.models import Listing

POINT = Point(21.2420, 55.8835, srid=4326)


def _listing(**kwargs):
    kwargs.setdefault("property_type", Listing.PropertyType.HOUSE)
    return baker.make(Listing, is_active=True, location=POINT, address_raw="T", **kwargs)


@pytest.mark.django_db
class TestClassifyNewListingsTask:
    @patch("apps.classifier.services._get_client")
    def test_reports_classified_and_failed(self, mock_get_client):
        mock_get_client.return_value = make_client(
            make_response(NARRATIVE_PAYLOAD),
            make_response(NARRATIVE_PAYLOAD, stop_reason="max_tokens"),
        )
        _listing()
        _listing()

        out = classify_new_listings(limit=10)

        assert out["classified"] == 1
        assert out["failed"] == 1
        assert out["errors"] == {"TruncatedOutputError": 1}
        assert out["by_verdict"] == {"review": 1}

    @patch("apps.classifier.services._get_client")
    def test_dead_model_is_reported_not_a_silent_zero(self, mock_get_client):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("model retired")
        mock_get_client.return_value = client
        _listing()

        out = classify_new_listings(limit=10)

        assert out["classified"] == 0
        assert out["failed"] == 1


@pytest.mark.django_db
class TestReclassifyAllTask:
    @patch("apps.classifier.services._get_client")
    def test_failed_reclassify_keeps_the_old_evaluation(self, mock_get_client):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("model retired")
        mock_get_client.return_value = client
        listing = _listing()
        ListingEvaluation.objects.create(
            listing=listing, verdict="match", match_score=0.9, summary="Old", model_used="x"
        )

        out = reclassify_all()

        assert out["failed"] == 1
        assert out["reclassified"] == 0
        assert ListingEvaluation.objects.filter(listing=listing).count() == 1


@pytest.mark.django_db
class TestCleanupTask:
    @patch("apps.classifier.services._get_client")
    def test_reports_counts(self, mock_get_client):
        mock_get_client.return_value = make_client(make_response(text="Namas."))
        _listing(description="SKUBIAI!!! Puiki investicija, nepraleiskite progos.")

        out = cleanup_descriptions(limit=10)

        assert out == {"cleaned": 1, "failed": 0, "errors": {}}
