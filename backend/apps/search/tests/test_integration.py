"""
End-to-end integration tests that exercise cross-app flows:

1. Scrape -> Search: listings in DB appear in search results
2. Classify -> Evaluate: classify a listing (mock Claude), verify /evaluated
3. Exclude flow: exclude listing, verify it disappears from search, un-exclude, verify it reappears
4. Saved search flow: create saved search, add matching listing, verify notification task
5. Full search: listing + permit + developer, call /search/full, verify all layers
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon
from django.test import Client
from django.utils import timezone
from model_bakery import baker

from apps.cadastre.models import HeritageObject, SpecialLandUseCondition
from apps.classifier.models import ListingEvaluation
from apps.developers.models import Developer
from apps.listings.models import ExcludedListing, Listing
from apps.permits.models import BuildingPermit
from apps.planning.models import PlanningDocument
from apps.search.models import SavedSearch

# --- Constants ---

KRETINGA_LNG, KRETINGA_LAT = 21.2420, 55.8835
VILNIUS_LNG, VILNIUS_LAT = 25.2797, 54.6872


# --- Helpers ---


def _make_small_polygon(lng: float, lat: float, size: float = 0.001) -> MultiPolygon:
    poly = Polygon.from_bbox((lng - size, lat - size, lng + size, lat + size))
    mpoly = MultiPolygon(poly)
    mpoly.srid = 4326
    return mpoly


def _make_mock_classify_response(
    verdict: str = "match",
    match_score: float = 0.85,
    summary: str = "Puikus namas.",
) -> MagicMock:
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "evaluate_listing"
    tool_block.input = {
        "verdict": verdict,
        "match_score": match_score,
        "summary": summary,
        "hard_filter_results": {
            "price_ok": True,
            "type_ok": True,
            "area_ok": True,
            "plot_ok": True,
            "location_ok": True,
        },
        "quality_notes": ["Good condition"],
        "red_flags": [],
    }
    response = MagicMock()
    response.content = [tool_block]
    return response


def _make_mock_extract_response() -> MagicMock:
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "extract_preferences"
    tool_block.input = {
        "patterns": [
            {"pattern": "Prefers houses with large plots", "weight": 1.5},
        ]
    }
    response = MagicMock()
    response.content = [tool_block]
    return response


def _make_kretinga_listing(**overrides) -> Listing:
    """Create a listing near Kretinga with sensible defaults."""
    defaults = {
        "title": "Namas Kretingoje",
        "price": Decimal("200000"),
        "area_sqm": Decimal("100"),
        "rooms": 4,
        "property_type": Listing.PropertyType.HOUSE,
        "listing_type": Listing.ListingType.SALE,
        "is_active": True,
        "is_new_construction": False,
        "city": "Kretinga",
        "address_raw": "Kretinga centras",
        "location": Point(KRETINGA_LNG, KRETINGA_LAT, srid=4326),
    }
    defaults.update(overrides)
    return baker.make(Listing, **defaults)


# --- Fixtures ---


@pytest.fixture
def api_client():
    return Client()


# ============================================================
# 1. Scrape -> Search flow
# ============================================================


@pytest.mark.django_db
class TestScrapeToSearch:
    """Simulate scraped listings appearing in DB and verify they appear in search."""

    def test_single_listing_found_in_search(self, api_client):
        """A listing created in the DB (as a pipeline would) appears in search."""
        listing = _make_kretinga_listing()

        resp = api_client.get(f"/api/search?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["results"][0]["id"] == listing.pk
        assert data["results"][0]["title"] == "Namas Kretingoje"

    def test_multiple_sources_all_appear(self, api_client):
        """Listings from different sources all appear in the same search."""
        _make_kretinga_listing(
            title="Domoplius namas",
            source="domoplius",
            source_id="dom-001",
        )
        _make_kretinga_listing(
            title="Aruodas namas",
            source="aruodas",
            source_id="aru-001",
        )
        _make_kretinga_listing(
            title="Skelbiu namas",
            source="skelbiu",
            source_id="ske-001",
        )

        resp = api_client.get(f"/api/search?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000")
        data = resp.json()
        assert data["count"] == 3
        titles = {r["title"] for r in data["results"]}
        assert titles == {"Domoplius namas", "Aruodas namas", "Skelbiu namas"}

    def test_inactive_listing_not_in_search(self, api_client):
        """A listing marked inactive (e.g., deactivated by pipeline) is excluded."""
        _make_kretinga_listing(is_active=False)

        resp = api_client.get(f"/api/search?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000")
        data = resp.json()
        assert data["count"] == 0

    def test_listing_outside_radius_not_in_search(self, api_client):
        """A listing in Vilnius does not appear in a Kretinga search."""
        _make_kretinga_listing(
            title="Vilnius namas",
            city="Vilnius",
            location=Point(VILNIUS_LNG, VILNIUS_LAT, srid=4326),
        )

        resp = api_client.get(f"/api/search?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000")
        data = resp.json()
        assert data["count"] == 0

    def test_filters_applied_to_scraped_data(self, api_client):
        """Price and property_type filters narrow down scraped results."""
        _make_kretinga_listing(
            title="Cheap flat",
            price=Decimal("50000"),
            property_type=Listing.PropertyType.FLAT,
        )
        _make_kretinga_listing(
            title="Expensive house",
            price=Decimal("400000"),
            property_type=Listing.PropertyType.HOUSE,
        )
        _make_kretinga_listing(
            title="Mid house",
            price=Decimal("200000"),
            property_type=Listing.PropertyType.HOUSE,
        )

        resp = api_client.get(
            f"/api/search?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000"
            "&property_type=house&min_price=100000&max_price=300000"
        )
        data = resp.json()
        assert data["count"] == 1
        assert data["results"][0]["title"] == "Mid house"

    def test_price_per_sqm_auto_calculated(self, api_client):
        """Pipeline-inserted listing gets price_per_sqm auto-calculated on save."""
        listing = _make_kretinga_listing(price=Decimal("200000"), area_sqm=Decimal("100"))
        listing.refresh_from_db()
        assert listing.price_per_sqm == Decimal("2000.00")

        resp = api_client.get(f"/api/search?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000")
        data = resp.json()
        assert data["results"][0]["price_per_sqm"] == 2000.0


# ============================================================
# 2. Classify -> Evaluate flow
# ============================================================


@pytest.mark.django_db
class TestClassifyToEvaluate:
    """Classify a listing via the API (mock Claude), then verify /evaluated endpoint."""

    @patch("apps.classifier.services._get_client")
    def test_classify_then_appears_in_evaluated(self, mock_get_client, api_client):
        """After classifying, the listing appears in /api/classifier/evaluated."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_mock_classify_response()
        mock_get_client.return_value = mock_client

        listing = _make_kretinga_listing()

        # Classify via API
        resp = api_client.post(f"/api/classifier/classify/{listing.pk}")
        assert resp.status_code == 200
        classify_data = resp.json()
        assert classify_data["verdict"] == "match"
        assert classify_data["match_score"] == 0.85
        assert classify_data["listing_id"] == listing.pk

        # Verify it shows in /evaluated
        resp = api_client.get("/api/classifier/evaluated")
        assert resp.status_code == 200
        eval_data = resp.json()
        assert len(eval_data) == 1
        assert eval_data[0]["listing_id"] == listing.pk
        assert eval_data[0]["verdict"] == "match"
        assert eval_data[0]["match_score"] == 0.85
        assert eval_data[0]["summary"] == "Puikus namas."

    @patch("apps.classifier.services._get_client")
    def test_classify_then_filter_by_verdict(self, mock_get_client, api_client):
        """Filtering /evaluated by verdict works after classification."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Classify listing1 as match
        listing1 = _make_kretinga_listing(title="Match namas")
        mock_client.messages.create.return_value = _make_mock_classify_response(
            verdict="match", match_score=0.9
        )
        api_client.post(f"/api/classifier/classify/{listing1.pk}")

        # Classify listing2 as skip
        listing2 = _make_kretinga_listing(title="Skip namas")
        mock_client.messages.create.return_value = _make_mock_classify_response(
            verdict="skip", match_score=0.2, summary="Netinka."
        )
        api_client.post(f"/api/classifier/classify/{listing2.pk}")

        # Filter by verdict=match
        resp = api_client.get("/api/classifier/evaluated?verdict=match")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["listing_title"] == "Match namas"

        # Filter by verdict=skip
        resp = api_client.get("/api/classifier/evaluated?verdict=skip")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["listing_title"] == "Skip namas"

    @patch("apps.classifier.services._get_client")
    def test_classify_then_filter_by_min_score(self, mock_get_client, api_client):
        """min_score filter on /evaluated works correctly."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        listing = _make_kretinga_listing(title="High score")
        mock_client.messages.create.return_value = _make_mock_classify_response(match_score=0.95)
        api_client.post(f"/api/classifier/classify/{listing.pk}")

        low_listing = _make_kretinga_listing(title="Low score")
        mock_client.messages.create.return_value = _make_mock_classify_response(
            verdict="skip", match_score=0.1, summary="Bad"
        )
        api_client.post(f"/api/classifier/classify/{low_listing.pk}")

        resp = api_client.get("/api/classifier/evaluated?min_score=0.5")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["listing_title"] == "High score"

    @patch("apps.classifier.services._get_client")
    def test_feedback_extracts_preferences_and_reclassifies(self, mock_get_client, api_client):
        """Submitting feedback extracts preferences and clears old evaluation."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        listing = _make_kretinga_listing()

        # First classify
        mock_client.messages.create.return_value = _make_mock_classify_response()
        api_client.post(f"/api/classifier/classify/{listing.pk}")
        assert ListingEvaluation.objects.filter(listing=listing).exists()

        # Then submit feedback — this should clear the evaluation
        mock_client.messages.create.return_value = _make_mock_extract_response()
        resp = api_client.post(
            f"/api/classifier/feedback/{listing.pk}",
            data=json.dumps({"feedback_type": "like", "reason": "Large plot"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        feedback_data = resp.json()
        assert feedback_data["feedback_type"] == "like"
        assert len(feedback_data["extracted_preferences"]) == 1
        assert "large plots" in feedback_data["extracted_preferences"][0].lower()

        # Evaluation should be deleted after feedback
        assert not ListingEvaluation.objects.filter(listing=listing).exists()

        # Verify preferences endpoint shows the extracted preference
        resp = api_client.get("/api/classifier/preferences")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["preference_type"] == "like"
        assert data[0]["weight"] == 1.5


# ============================================================
# 3. Exclude flow
# ============================================================


@pytest.mark.django_db
class TestExcludeFlow:
    """Exclude a listing, verify it disappears from search and full search,
    un-exclude it, verify it reappears."""

    def test_exclude_removes_from_basic_search(self, api_client):
        """After excluding, listing no longer appears in /search."""
        listing = _make_kretinga_listing()

        # Listing is visible initially
        resp = api_client.get(f"/api/search?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000")
        assert resp.json()["count"] == 1

        # Exclude it
        resp = api_client.post(
            f"/api/exclude/{listing.pk}",
            data=json.dumps({"reason": "Too expensive"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        exclude_data = resp.json()
        assert exclude_data["listing_id"] == listing.pk
        assert exclude_data["reason"] == "Too expensive"

        # No longer in search
        resp = api_client.get(f"/api/search?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000")
        assert resp.json()["count"] == 0

    def test_exclude_removes_from_full_search(self, api_client):
        """Excluded listings are also absent from /search/full."""
        listing = _make_kretinga_listing()

        # Visible in full search
        resp = api_client.get(
            f"/api/search/full?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000"
        )
        assert len(resp.json()["listings"]) == 1

        # Exclude
        api_client.post(
            f"/api/exclude/{listing.pk}",
            data=json.dumps({"reason": "Bad condition"}),
            content_type="application/json",
        )

        # Gone from full search
        resp = api_client.get(
            f"/api/search/full?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000"
        )
        assert len(resp.json()["listings"]) == 0

    def test_unexclude_restores_to_search(self, api_client):
        """After un-excluding, the listing reappears in search."""
        listing = _make_kretinga_listing()

        # Exclude
        api_client.post(
            f"/api/exclude/{listing.pk}",
            data=json.dumps({"reason": "Changed my mind later"}),
            content_type="application/json",
        )
        resp = api_client.get(f"/api/search?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000")
        assert resp.json()["count"] == 0

        # Un-exclude
        resp = api_client.delete(f"/api/exclude/{listing.pk}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Back in search
        resp = api_client.get(f"/api/search?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000")
        assert resp.json()["count"] == 1

    def test_unexclude_nonexcluded_listing(self, api_client):
        """Un-excluding a listing that is not excluded returns ok=False."""
        listing = _make_kretinga_listing()

        resp = api_client.delete(f"/api/exclude/{listing.pk}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False

    def test_exclude_only_affects_target_listing(self, api_client):
        """Excluding one listing does not affect others."""
        l1 = _make_kretinga_listing(title="Excluded one")
        _make_kretinga_listing(title="Not excluded")

        api_client.post(
            f"/api/exclude/{l1.pk}",
            data=json.dumps({"reason": "Not interested"}),
            content_type="application/json",
        )

        resp = api_client.get(f"/api/search?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000")
        data = resp.json()
        assert data["count"] == 1
        assert data["results"][0]["title"] == "Not excluded"

    def test_exclude_idempotent(self, api_client):
        """Excluding an already-excluded listing updates the reason."""
        listing = _make_kretinga_listing()

        api_client.post(
            f"/api/exclude/{listing.pk}",
            data=json.dumps({"reason": "First reason"}),
            content_type="application/json",
        )
        resp = api_client.post(
            f"/api/exclude/{listing.pk}",
            data=json.dumps({"reason": "Updated reason"}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        assert resp.json()["reason"] == "Updated reason"

        assert ExcludedListing.objects.filter(listing=listing).count() == 1


# ============================================================
# 4. Saved search flow
# ============================================================


@pytest.mark.django_db
class TestSavedSearchFlow:
    """Create a saved search, add a matching listing, run the notification task."""

    def test_create_search_add_listing_notify(self, api_client):
        """End-to-end: create saved search via API, add listing, run notify task."""
        # Create saved search via API
        resp = api_client.post(
            "/api/searches",
            data=json.dumps(
                {
                    "name": "Kretinga namai",
                    "lat": KRETINGA_LAT,
                    "lng": KRETINGA_LNG,
                    "radius_m": 5000,
                    "min_price": 100000,
                    "max_price": 300000,
                    "property_type": "house",
                    "listing_type": "sale",
                }
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        search_id = resp.json()["id"]

        # Push last_notified_at to the past
        past = timezone.now() - timezone.timedelta(hours=1)
        SavedSearch.objects.filter(pk=search_id).update(last_notified_at=past)

        # Add a matching listing
        _make_kretinga_listing(
            title="Puikus namas",
            price=Decimal("200000"),
            property_type=Listing.PropertyType.HOUSE,
            listing_type=Listing.ListingType.SALE,
        )

        # Run the notification task
        from apps.search.tasks import notify_saved_searches

        with patch("apps.search.tasks.send_mail") as mock_send_mail:
            result = notify_saved_searches()

        assert result["searches_checked"] == 1
        assert result["notified"] == 1
        assert result["listings_sent"] == 1
        mock_send_mail.assert_called_once()
        assert "Puikus namas" in mock_send_mail.call_args.kwargs["message"]

    def test_saved_search_filters_exclude_non_matching(self, api_client):
        """Saved search filters exclude listings that don't match."""
        resp = api_client.post(
            "/api/searches",
            data=json.dumps(
                {
                    "name": "Houses only",
                    "lat": KRETINGA_LAT,
                    "lng": KRETINGA_LNG,
                    "radius_m": 5000,
                    "property_type": "house",
                    "listing_type": "sale",
                }
            ),
            content_type="application/json",
        )
        search_id = resp.json()["id"]

        past = timezone.now() - timezone.timedelta(hours=1)
        SavedSearch.objects.filter(pk=search_id).update(last_notified_at=past)

        # Add a flat (not a house) — should NOT match
        _make_kretinga_listing(
            title="Butas",
            property_type=Listing.PropertyType.FLAT,
        )

        from apps.search.tasks import notify_saved_searches

        with patch("apps.search.tasks.send_mail") as mock_send_mail:
            result = notify_saved_searches()

        assert result["notified"] == 0
        mock_send_mail.assert_not_called()

    def test_saved_search_location_filter(self, api_client):
        """Listing outside the search radius does not trigger notification."""
        resp = api_client.post(
            "/api/searches",
            data=json.dumps(
                {
                    "name": "Kretinga area",
                    "lat": KRETINGA_LAT,
                    "lng": KRETINGA_LNG,
                    "radius_m": 5000,
                }
            ),
            content_type="application/json",
        )
        search_id = resp.json()["id"]

        past = timezone.now() - timezone.timedelta(hours=1)
        SavedSearch.objects.filter(pk=search_id).update(last_notified_at=past)

        # Add listing in Vilnius — outside radius
        _make_kretinga_listing(
            title="Vilnius namas",
            city="Vilnius",
            location=Point(VILNIUS_LNG, VILNIUS_LAT, srid=4326),
        )

        from apps.search.tasks import notify_saved_searches

        with patch("apps.search.tasks.send_mail") as mock_send_mail:
            result = notify_saved_searches()

        assert result["notified"] == 0
        mock_send_mail.assert_not_called()

    def test_delete_saved_search_stops_notifications(self, api_client):
        """After deleting a saved search, the notification task skips it."""
        resp = api_client.post(
            "/api/searches",
            data=json.dumps(
                {
                    "name": "To be deleted",
                    "lat": KRETINGA_LAT,
                    "lng": KRETINGA_LNG,
                    "radius_m": 5000,
                }
            ),
            content_type="application/json",
        )
        search_id = resp.json()["id"]

        # Delete it
        resp = api_client.delete(f"/api/searches/{search_id}")
        assert resp.status_code == 200

        from apps.search.tasks import notify_saved_searches

        result = notify_saved_searches()
        assert result["searches_checked"] == 0


# ============================================================
# 5. Full search — all layers
# ============================================================


@pytest.mark.django_db
class TestFullSearchAllLayers:
    """Create data across all layers, call /search/full, verify everything returns."""

    def test_full_search_returns_all_layers(self, api_client):
        """Listing + permit + developer all appear in a single /search/full call."""
        listing = _make_kretinga_listing()

        developer = baker.make(
            Developer,
            company_code="300111222",
            name="UAB StatybaPlus",
            nace_codes=["41.10"],
            registered_address="Kretinga, Test g. 1",
            registered_address_point=Point(KRETINGA_LNG, KRETINGA_LAT, srid=4326),
            status="active",
        )

        baker.make(
            BuildingPermit,
            permit_number="LSNS-01-24-9999",
            applicant_name="UAB StatybaPlus",
            applicant=developer,
            status="issued",
            issued_at=date(2024, 6, 15),
            building_purpose="residential",
            project_type="new",
            cadastral_number="5530/0001:0001",
            location=Point(KRETINGA_LNG, KRETINGA_LAT, srid=4326),
            source_url="https://planuojustatyti.lt/test",
        )

        resp = api_client.get(
            f"/api/search/full?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000"
        )
        assert resp.status_code == 200
        data = resp.json()

        # Listings
        assert len(data["listings"]) == 1
        assert data["listings"][0]["id"] == listing.pk

        # Permits
        assert len(data["permits"]) == 1
        assert data["permits"][0]["permit_number"] == "LSNS-01-24-9999"
        assert data["permits"][0]["status"] == "issued"

        # Developers
        assert len(data["developers"]) == 1
        assert data["developers"][0]["name"] == "UAB StatybaPlus"
        assert data["developers"][0]["company_code"] == "300111222"
        assert data["developers"][0]["active_permits_count"] == 1

    def test_full_search_with_planning_and_restrictions(self, api_client):
        """Planning docs, heritage, and special land use all appear."""
        baker.make(
            PlanningDocument,
            tpdris_id="TP-INT-001",
            title="Kretingos detalusis planas",
            doc_type="detailed",
            status="approved",
            municipality="Kretinga",
            max_floors=3,
            allowed_uses=["residential", "commercial"],
            boundary=_make_small_polygon(KRETINGA_LNG, KRETINGA_LAT),
            source_url="https://tpdris.lt/test",
        )

        baker.make(
            HeritageObject,
            kvr_code="KVR-INT-001",
            name="Kretingos dvaro parkas",
            category="Kultūros paminklas",
            protection_level="valstybinis",
            geometry=_make_small_polygon(KRETINGA_LNG, KRETINGA_LAT),
        )

        baker.make(
            SpecialLandUseCondition,
            category="Vandens apsaugos zona (50m)",
            description="River protection zone",
            geometry=_make_small_polygon(KRETINGA_LNG, KRETINGA_LAT),
        )

        resp = api_client.get(
            f"/api/search/full?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000"
        )
        data = resp.json()

        # Planning
        assert len(data["planning"]) == 1
        assert data["planning"][0]["title"] == "Kretingos detalusis planas"
        assert data["planning"][0]["max_floors"] == 3
        assert data["planning"][0]["allowed_uses"] == ["residential", "commercial"]

        # Heritage
        assert len(data["restrictions"]["heritage"]) == 1
        assert data["restrictions"]["heritage"][0]["name"] == "Kretingos dvaro parkas"
        assert data["restrictions"]["heritage"][0]["protection_level"] == "valstybinis"

        # Special land use
        assert len(data["restrictions"]["special_land_use"]) == 1
        assert "Vandens" in data["restrictions"]["special_land_use"][0]["category"]

    def test_full_search_excluded_listing_not_in_results(self, api_client):
        """Excluded listings should not appear in /search/full either."""
        listing = _make_kretinga_listing()
        ExcludedListing.objects.create(listing=listing, reason="Bad area")

        resp = api_client.get(
            f"/api/search/full?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000"
        )
        data = resp.json()
        assert len(data["listings"]) == 0

    def test_full_search_distant_data_excluded(self, api_client):
        """Data near Kretinga should not appear in a Vilnius-centered search."""
        _make_kretinga_listing()

        baker.make(
            Developer,
            company_code="300999888",
            name="UAB Kretingos Statyba",
            registered_address_point=Point(KRETINGA_LNG, KRETINGA_LAT, srid=4326),
            status="active",
        )

        baker.make(
            BuildingPermit,
            permit_number="LSNS-DISTANT-001",
            location=Point(KRETINGA_LNG, KRETINGA_LAT, srid=4326),
            source_url="https://planuojustatyti.lt/distant",
        )

        resp = api_client.get(f"/api/search/full?lat={VILNIUS_LAT}&lng={VILNIUS_LNG}&radius_m=5000")
        data = resp.json()
        assert data["listings"] == []
        assert data["permits"] == []
        assert data["developers"] == []

    def test_full_search_all_layers_populated(self, api_client):
        """End-to-end: every layer type has data in a single search."""
        _make_kretinga_listing()

        developer = baker.make(
            Developer,
            company_code="300222333",
            name="UAB VisoPlanas",
            registered_address_point=Point(KRETINGA_LNG, KRETINGA_LAT, srid=4326),
            status="active",
        )

        baker.make(
            BuildingPermit,
            permit_number="LSNS-ALL-001",
            applicant=developer,
            applicant_name="UAB VisoPlanas",
            status="issued",
            issued_at=date(2024, 1, 1),
            location=Point(KRETINGA_LNG, KRETINGA_LAT, srid=4326),
            source_url="https://planuojustatyti.lt/all",
        )

        baker.make(
            PlanningDocument,
            tpdris_id="TP-ALL-001",
            title="Visas planas",
            doc_type="master",
            status="approved",
            municipality="Kretinga",
            boundary=_make_small_polygon(KRETINGA_LNG, KRETINGA_LAT),
            source_url="https://tpdris.lt/all",
        )

        baker.make(
            HeritageObject,
            kvr_code="KVR-ALL-001",
            name="Paveldo objektas",
            protection_level="valstybinis",
            geometry=_make_small_polygon(KRETINGA_LNG, KRETINGA_LAT),
        )

        baker.make(
            SpecialLandUseCondition,
            category="Apsaugos zona",
            description="Test zone",
            geometry=_make_small_polygon(KRETINGA_LNG, KRETINGA_LAT),
        )

        resp = api_client.get(
            f"/api/search/full?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000"
        )
        data = resp.json()

        assert len(data["listings"]) >= 1
        assert len(data["permits"]) >= 1
        assert len(data["developers"]) >= 1
        assert len(data["planning"]) >= 1
        assert len(data["restrictions"]["heritage"]) >= 1
        assert len(data["restrictions"]["special_land_use"]) >= 1
        assert data["center"]["lat"] == KRETINGA_LAT
        assert data["center"]["lng"] == KRETINGA_LNG

    def test_developer_permit_count_reflects_status(self, api_client):
        """active_permits_count only counts issued/in_progress permits."""
        developer = baker.make(
            Developer,
            company_code="300555666",
            name="UAB CountTest",
            registered_address_point=Point(KRETINGA_LNG, KRETINGA_LAT, srid=4326),
            status="active",
        )

        # Issued permit — should count
        baker.make(
            BuildingPermit,
            permit_number="COUNT-001",
            applicant=developer,
            status="issued",
            location=Point(KRETINGA_LNG, KRETINGA_LAT, srid=4326),
            source_url="https://test.lt/1",
        )

        # Revoked permit — should NOT count
        baker.make(
            BuildingPermit,
            permit_number="COUNT-002",
            applicant=developer,
            status="revoked",
            location=Point(KRETINGA_LNG, KRETINGA_LAT, srid=4326),
            source_url="https://test.lt/2",
        )

        # In-progress permit — should count
        baker.make(
            BuildingPermit,
            permit_number="COUNT-003",
            applicant=developer,
            status="in_progress",
            location=Point(KRETINGA_LNG, KRETINGA_LAT, srid=4326),
            source_url="https://test.lt/3",
        )

        resp = api_client.get(
            f"/api/search/full?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000"
        )
        data = resp.json()
        assert len(data["developers"]) == 1
        assert data["developers"][0]["active_permits_count"] == 2


# ============================================================
# Cross-cutting: Classify + Exclude interaction
# ============================================================


@pytest.mark.django_db
class TestClassifyAndExcludeInteraction:
    """Verify that classification and exclusion work together correctly."""

    @patch("apps.classifier.services._get_client")
    def test_excluded_listing_still_has_evaluation(self, mock_get_client, api_client):
        """An excluded listing retains its evaluation in /evaluated."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_mock_classify_response()
        mock_get_client.return_value = mock_client

        listing = _make_kretinga_listing()

        # Classify it
        api_client.post(f"/api/classifier/classify/{listing.pk}")

        # Exclude it
        api_client.post(
            f"/api/exclude/{listing.pk}",
            data=json.dumps({"reason": "Test"}),
            content_type="application/json",
        )

        # Not in search
        resp = api_client.get(f"/api/search?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000")
        assert resp.json()["count"] == 0

        # But still in evaluated
        resp = api_client.get("/api/classifier/evaluated")
        data = resp.json()
        assert len(data) == 1
        assert data[0]["listing_id"] == listing.pk
