from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import requests as req_lib
from django.contrib.gis.geos import Point
from django.test import Client
from model_bakery import baker

from apps.listings.models import Listing


@pytest.fixture
def api_client():
    return Client()


@pytest.fixture
def kretinga_listings():
    """3 listings near Kretinga, 1 in Vilnius."""
    listings = [
        baker.make(
            Listing,
            title="Namas centre",
            price=Decimal("200000"),
            area_sqm=Decimal("100"),
            rooms=4,
            property_type=Listing.PropertyType.HOUSE,
            listing_type=Listing.ListingType.SALE,
            is_new_construction=True,
            is_active=True,
            city="Kretinga",
            address_raw="Kretinga centras",
            location=Point(21.2420, 55.8835, srid=4326),
        ),
        baker.make(
            Listing,
            title="Namas šiaurėje",
            price=Decimal("180000"),
            area_sqm=Decimal("95"),
            rooms=3,
            property_type=Listing.PropertyType.HOUSE,
            listing_type=Listing.ListingType.SALE,
            is_new_construction=True,
            is_active=True,
            city="Kretinga",
            address_raw="Kretinga šiaurė",
            location=Point(21.2550, 55.8900, srid=4326),
        ),
        baker.make(
            Listing,
            title="Butas Kretingoje",
            price=Decimal("80000"),
            area_sqm=Decimal("50"),
            rooms=2,
            property_type=Listing.PropertyType.FLAT,
            listing_type=Listing.ListingType.SALE,
            is_new_construction=False,
            is_active=True,
            city="Kretinga",
            address_raw="Kretinga butas",
            location=Point(21.2400, 55.8850, srid=4326),
        ),
        baker.make(
            Listing,
            title="Namas Vilniuje",
            price=Decimal("300000"),
            area_sqm=Decimal("120"),
            rooms=5,
            property_type=Listing.PropertyType.HOUSE,
            listing_type=Listing.ListingType.SALE,
            is_active=True,
            city="Vilnius",
            address_raw="Vilnius centras",
            location=Point(25.2797, 54.6872, srid=4326),
        ),
    ]
    return listings


@pytest.mark.django_db
class TestSearchEndpoint:
    def test_basic_search(self, api_client, kretinga_listings):
        resp = api_client.get("/api/search?lat=55.8835&lng=21.2420&radius_m=5000")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        assert data["center"] == {"lat": 55.8835, "lng": 21.242}
        assert data["radius_m"] == 5000

    def test_results_sorted_by_distance(self, api_client, kretinga_listings):
        resp = api_client.get("/api/search?lat=55.8835&lng=21.2420&radius_m=5000")
        data = resp.json()
        distances = [r["distance_m"] for r in data["results"]]
        assert distances == sorted(distances)

    def test_results_have_distance(self, api_client, kretinga_listings):
        resp = api_client.get("/api/search?lat=55.8835&lng=21.2420&radius_m=5000")
        data = resp.json()
        for r in data["results"]:
            assert "distance_m" in r
            assert r["distance_m"] >= 0

    def test_excludes_far_listings(self, api_client, kretinga_listings):
        resp = api_client.get("/api/search?lat=55.8835&lng=21.2420&radius_m=5000")
        data = resp.json()
        titles = [r["title"] for r in data["results"]]
        assert "Namas Vilniuje" not in titles

    def test_excludes_inactive(self, api_client, kretinga_listings):
        kretinga_listings[0].is_active = False
        kretinga_listings[0].save()
        resp = api_client.get("/api/search?lat=55.8835&lng=21.2420&radius_m=5000")
        data = resp.json()
        assert data["count"] == 2

    def test_filter_by_price_range(self, api_client, kretinga_listings):
        resp = api_client.get(
            "/api/search?lat=55.8835&lng=21.2420&radius_m=5000&min_price=100000&max_price=250000"
        )
        data = resp.json()
        assert data["count"] == 2
        for r in data["results"]:
            assert r["price"] >= 100000
            assert r["price"] <= 250000

    def test_filter_by_rooms(self, api_client, kretinga_listings):
        resp = api_client.get("/api/search?lat=55.8835&lng=21.2420&radius_m=5000&rooms=4")
        data = resp.json()
        assert data["count"] == 1
        assert data["results"][0]["rooms"] == 4

    def test_filter_by_property_type(self, api_client, kretinga_listings):
        resp = api_client.get(
            "/api/search?lat=55.8835&lng=21.2420&radius_m=5000&property_type=flat"
        )
        data = resp.json()
        assert data["count"] == 1
        assert data["results"][0]["property_type"] == "flat"

    def test_filter_by_listing_type(self, api_client, kretinga_listings):
        resp = api_client.get("/api/search?lat=55.8835&lng=21.2420&radius_m=5000&listing_type=sale")
        data = resp.json()
        assert data["count"] == 3

    def test_filter_new_construction(self, api_client, kretinga_listings):
        resp = api_client.get(
            "/api/search?lat=55.8835&lng=21.2420&radius_m=5000&is_new_construction=true"
        )
        data = resp.json()
        assert data["count"] == 2
        for r in data["results"]:
            assert r["is_new_construction"] is True

    def test_radius_capped_at_max(self, api_client, kretinga_listings):
        resp = api_client.get("/api/search?lat=55.8835&lng=21.2420&radius_m=999999")
        data = resp.json()
        assert data["radius_m"] == 50000

    def test_empty_results(self, api_client):
        resp = api_client.get("/api/search?lat=55.8835&lng=21.2420&radius_m=100")
        data = resp.json()
        assert data["count"] == 0
        assert data["results"] == []

    def test_result_shape(self, api_client, kretinga_listings):
        resp = api_client.get("/api/search?lat=55.8835&lng=21.2420&radius_m=5000")
        data = resp.json()
        r = data["results"][0]
        assert "id" in r
        assert "lat" in r
        assert "lng" in r
        assert "source_url" in r
        assert "photo_urls" in r


@pytest.mark.django_db
class TestGeocodeEndpoint:
    @patch("apps.search.api.requests.get")
    def test_geocode_success(self, mock_get, api_client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {
                "lat": "55.8835",
                "lon": "21.2420",
                "display_name": "Kretinga, Kretingos rajono savivaldybė",
            }
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        resp = api_client.get("/api/geocode?q=Kretinga")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["lat"] == 55.8835
        assert data[0]["lng"] == 21.2420
        assert "Kretinga" in data[0]["display_name"]

    @patch("apps.search.api.requests.get")
    def test_geocode_nominatim_error(self, mock_get, api_client):
        mock_get.side_effect = req_lib.ConnectionError("Connection refused")
        resp = api_client.get("/api/geocode?q=test")
        assert resp.status_code == 200
        assert resp.json() == []

    @patch("apps.search.api.requests.get")
    def test_geocode_passes_country_filter(self, mock_get, api_client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        api_client.get("/api/geocode?q=Kretinga")
        call_kwargs = mock_get.call_args
        assert call_kwargs.kwargs["params"]["countrycodes"] == "lt"
