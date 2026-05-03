import pytest
from django.contrib.gis.geos import Point
from django.db import IntegrityError
from django.test import Client
from model_bakery import baker

from apps.listings.models import ExcludedListing, Listing


@pytest.mark.django_db
class TestExcludedListingModel:
    def test_create_exclusion(self):
        listing = baker.make(
            Listing,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga",
        )
        exclusion = ExcludedListing.objects.create(
            listing=listing,
            reason="Too expensive",
        )
        assert exclusion.pk is not None
        assert exclusion.reason == "Too expensive"
        assert exclusion.excluded_at is not None
        assert str(exclusion).startswith("Excluded:")

    def test_one_exclusion_per_listing(self):
        listing = baker.make(
            Listing,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga",
        )
        ExcludedListing.objects.create(listing=listing, reason="reason 1")
        with pytest.raises(IntegrityError):
            ExcludedListing.objects.create(listing=listing, reason="reason 2")

    def test_cascade_delete(self):
        listing = baker.make(
            Listing,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga",
        )
        ExcludedListing.objects.create(listing=listing, reason="test")
        assert ExcludedListing.objects.count() == 1
        listing.delete()
        assert ExcludedListing.objects.count() == 0

    def test_excluded_listings_filtered_from_queryset(self):
        included = baker.make(
            Listing,
            is_active=True,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Included",
        )
        excluded = baker.make(
            Listing,
            is_active=True,
            location=Point(21.2430, 55.8840, srid=4326),
            address_raw="Excluded",
        )
        ExcludedListing.objects.create(listing=excluded, reason="Bad location")

        qs = Listing.objects.filter(is_active=True).exclude(exclusion__isnull=False)
        assert qs.count() == 1
        assert qs.first().pk == included.pk


@pytest.mark.django_db
class TestExcludeAPI:
    def test_exclude_listing(self):
        listing = baker.make(
            Listing,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga",
        )
        client = Client()
        resp = client.post(
            f"/api/exclude/{listing.id}",
            data={"reason": "Too small"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["listing_id"] == listing.id
        assert data["reason"] == "Too small"
        assert ExcludedListing.objects.filter(listing=listing).exists()

    def test_exclude_listing_updates_reason(self):
        listing = baker.make(
            Listing,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga",
        )
        ExcludedListing.objects.create(listing=listing, reason="Original reason")

        client = Client()
        resp = client.post(
            f"/api/exclude/{listing.id}",
            data={"reason": "Updated reason"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        exclusion = ExcludedListing.objects.get(listing=listing)
        assert exclusion.reason == "Updated reason"

    def test_unexclude_listing(self):
        listing = baker.make(
            Listing,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga",
        )
        ExcludedListing.objects.create(listing=listing, reason="Test")

        client = Client()
        resp = client.delete(f"/api/exclude/{listing.id}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert not ExcludedListing.objects.filter(listing=listing).exists()

    def test_unexclude_nonexcluded_listing(self):
        listing = baker.make(
            Listing,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga",
        )
        client = Client()
        resp = client.delete(f"/api/exclude/{listing.id}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_exclude_nonexistent_listing_returns_404(self):
        client = Client()
        resp = client.post(
            "/api/exclude/99999",
            data={"reason": "Nope"},
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_search_excludes_excluded_listings(self, kretinga_point):
        baker.make(
            Listing,
            is_active=True,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Visible listing",
        )
        excluded_listing = baker.make(
            Listing,
            is_active=True,
            location=Point(21.2430, 55.8840, srid=4326),
            address_raw="Excluded listing",
        )
        ExcludedListing.objects.create(listing=excluded_listing, reason="Bad")

        client = Client()
        resp = client.get("/api/search", {"lat": 55.8835, "lng": 21.2420, "radius_m": 5000})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        listing_ids = [r["id"] for r in data["results"]]
        assert excluded_listing.id not in listing_ids
