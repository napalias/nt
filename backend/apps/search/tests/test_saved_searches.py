from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.gis.geos import Point
from django.test import Client
from django.utils import timezone
from model_bakery import baker

from apps.listings.models import Listing
from apps.search.models import SavedSearch


@pytest.fixture
def api_client():
    return Client()


@pytest.fixture
def saved_search():
    return SavedSearch.objects.create(
        name="Kretinga namai",
        lat=55.8835,
        lng=21.2420,
        radius_m=5000,
        min_price=Decimal("100000"),
        max_price=Decimal("300000"),
        property_type="house",
        listing_type="sale",
    )


# --- Model tests ---


@pytest.mark.django_db
class TestSavedSearchModel:
    def test_create_saved_search(self):
        search = SavedSearch.objects.create(
            name="Test search",
            lat=55.0,
            lng=21.0,
        )
        assert search.pk is not None
        assert search.name == "Test search"
        assert search.radius_m == 5000
        assert search.listing_type == "sale"
        assert search.is_active is True
        assert search.created_at is not None
        assert search.last_notified_at is not None

    def test_str_representation(self, saved_search):
        assert str(saved_search) == "Kretinga namai"

    def test_nullable_fields(self):
        search = SavedSearch.objects.create(
            name="Minimal",
            lat=55.0,
            lng=21.0,
        )
        assert search.min_price is None
        assert search.max_price is None
        assert search.rooms is None
        assert search.is_new_construction is None
        assert search.property_type == ""

    def test_ordering_by_created_at_desc(self):
        s1 = SavedSearch.objects.create(name="First", lat=55.0, lng=21.0)
        s2 = SavedSearch.objects.create(name="Second", lat=55.0, lng=21.0)
        searches = list(SavedSearch.objects.all())
        assert searches[0].pk == s2.pk
        assert searches[1].pk == s1.pk


# --- API tests ---


@pytest.mark.django_db
class TestSavedSearchAPI:
    def test_create_saved_search(self, api_client):
        resp = api_client.post(
            "/api/searches",
            data={
                "name": "Kretinga namai",
                "lat": 55.8835,
                "lng": 21.2420,
                "radius_m": 5000,
                "min_price": 100000,
                "max_price": 300000,
                "property_type": "house",
                "listing_type": "sale",
            },
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Kretinga namai"
        assert data["lat"] == 55.8835
        assert data["lng"] == 21.2420
        assert data["radius_m"] == 5000
        assert data["min_price"] == 100000.0
        assert data["max_price"] == 300000.0
        assert data["property_type"] == "house"
        assert data["listing_type"] == "sale"
        assert data["is_active"] is True
        assert SavedSearch.objects.count() == 1

    def test_create_minimal_saved_search(self, api_client):
        resp = api_client.post(
            "/api/searches",
            data={"name": "Minimal", "lat": 55.0, "lng": 21.0},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["min_price"] is None
        assert data["max_price"] is None
        assert data["rooms"] is None
        assert data["is_new_construction"] is None

    def test_list_saved_searches(self, api_client, saved_search):
        resp = api_client.get("/api/searches")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Kretinga namai"
        assert data[0]["id"] == saved_search.pk

    def test_list_empty(self, api_client):
        resp = api_client.get("/api/searches")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_delete_saved_search(self, api_client, saved_search):
        resp = api_client.delete(f"/api/searches/{saved_search.pk}")
        assert resp.status_code == 200
        assert SavedSearch.objects.count() == 0

    def test_delete_nonexistent(self, api_client):
        resp = api_client.delete("/api/searches/999")
        assert resp.status_code == 404


# --- Notification task tests ---


@pytest.mark.django_db
class TestNotifySavedSearches:
    def test_no_active_searches(self):
        from apps.search.tasks import notify_saved_searches

        result = notify_saved_searches()
        assert result["searches_checked"] == 0
        assert result["notified"] == 0

    def test_no_matching_listings(self, saved_search):
        from apps.search.tasks import notify_saved_searches

        result = notify_saved_searches()
        assert result["searches_checked"] == 1
        assert result["notified"] == 0

    @patch("apps.search.tasks.send_mail")
    def test_sends_email_for_new_listings(self, mock_send_mail, saved_search):
        from apps.search.tasks import notify_saved_searches

        # Set last_notified_at to the past so new listings match
        past = timezone.now() - timezone.timedelta(hours=1)
        SavedSearch.objects.filter(pk=saved_search.pk).update(last_notified_at=past)
        saved_search.refresh_from_db()

        baker.make(
            Listing,
            title="Naujas namas",
            price=Decimal("200000"),
            property_type="house",
            listing_type="sale",
            is_active=True,
            location=Point(21.2420, 55.8835, srid=4326),
        )

        result = notify_saved_searches()
        assert result["searches_checked"] == 1
        assert result["notified"] == 1
        assert result["listings_sent"] == 1
        mock_send_mail.assert_called_once()

        call_kwargs = mock_send_mail.call_args
        assert "Kretinga namai" in call_kwargs.kwargs["subject"]
        assert "Naujas namas" in call_kwargs.kwargs["message"]

    @patch("apps.search.tasks.send_mail")
    def test_updates_last_notified_at(self, mock_send_mail, saved_search):
        from apps.search.tasks import notify_saved_searches

        past = timezone.now() - timezone.timedelta(hours=1)
        SavedSearch.objects.filter(pk=saved_search.pk).update(last_notified_at=past)
        saved_search.refresh_from_db()
        old_notified = saved_search.last_notified_at

        baker.make(
            Listing,
            title="Namas",
            price=Decimal("200000"),
            property_type="house",
            listing_type="sale",
            is_active=True,
            location=Point(21.2420, 55.8835, srid=4326),
        )

        notify_saved_searches()
        saved_search.refresh_from_db()
        assert saved_search.last_notified_at > old_notified

    @patch("apps.search.tasks.send_mail")
    def test_skips_inactive_searches(self, mock_send_mail, saved_search):
        from apps.search.tasks import notify_saved_searches

        saved_search.is_active = False
        saved_search.save()

        result = notify_saved_searches()
        assert result["searches_checked"] == 0
        mock_send_mail.assert_not_called()

    @patch("apps.search.tasks.send_mail")
    def test_filters_by_price(self, mock_send_mail, saved_search):
        from apps.search.tasks import notify_saved_searches

        past = timezone.now() - timezone.timedelta(hours=1)
        SavedSearch.objects.filter(pk=saved_search.pk).update(last_notified_at=past)

        # Too expensive — should not match
        baker.make(
            Listing,
            title="Brangus namas",
            price=Decimal("500000"),
            property_type="house",
            listing_type="sale",
            is_active=True,
            location=Point(21.2420, 55.8835, srid=4326),
        )

        result = notify_saved_searches()
        assert result["notified"] == 0
        mock_send_mail.assert_not_called()

    @patch("apps.search.tasks.send_mail")
    def test_filters_by_location(self, mock_send_mail, saved_search):
        from apps.search.tasks import notify_saved_searches

        past = timezone.now() - timezone.timedelta(hours=1)
        SavedSearch.objects.filter(pk=saved_search.pk).update(last_notified_at=past)

        # Vilnius — too far
        baker.make(
            Listing,
            title="Vilnius namas",
            price=Decimal("200000"),
            property_type="house",
            listing_type="sale",
            is_active=True,
            location=Point(25.2797, 54.6872, srid=4326),
        )

        result = notify_saved_searches()
        assert result["notified"] == 0
        mock_send_mail.assert_not_called()
