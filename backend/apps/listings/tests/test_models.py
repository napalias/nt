from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.db import IntegrityError
from model_bakery import baker

from apps.listings.models import Listing


@pytest.mark.django_db
class TestListingModel:
    def test_create_listing(self):
        listing = baker.make(
            Listing,
            title="Namas Kretingoje",
            source="domoplius",
            source_id="DOM-001",
            property_type=Listing.PropertyType.HOUSE,
            listing_type=Listing.ListingType.SALE,
            price=Decimal("200000"),
            area_sqm=Decimal("100"),
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga",
        )
        assert listing.pk is not None
        assert str(listing) == "Namas Kretingoje (domoplius)"

    def test_price_per_sqm_auto_calculated(self):
        listing = baker.make(
            Listing,
            price=Decimal("200000"),
            area_sqm=Decimal("100"),
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga",
        )
        assert listing.price_per_sqm == Decimal("2000.00")

    def test_price_per_sqm_cleared_when_price_removed(self):
        listing = baker.make(
            Listing,
            price=Decimal("200000"),
            area_sqm=Decimal("100"),
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga",
        )
        assert listing.price_per_sqm == Decimal("2000.00")
        listing.price = None
        listing.save()
        listing.refresh_from_db()
        assert listing.price_per_sqm is None

    def test_price_per_sqm_none_when_no_area(self):
        listing = baker.make(
            Listing,
            price=Decimal("200000"),
            area_sqm=None,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga",
        )
        assert listing.price_per_sqm is None

    def test_unique_source_listing_constraint(self):
        baker.make(
            Listing,
            source="domoplius",
            source_id="DOM-001",
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga",
        )
        with pytest.raises(IntegrityError):
            baker.make(
                Listing,
                source="domoplius",
                source_id="DOM-001",
                location=Point(21.2420, 55.8835, srid=4326),
                address_raw="Kretinga",
            )

    def test_different_sources_same_id_allowed(self):
        baker.make(
            Listing,
            source="domoplius",
            source_id="001",
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga",
        )
        listing2 = baker.make(
            Listing,
            source="aruodas",
            source_id="001",
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga",
        )
        assert listing2.pk is not None


@pytest.mark.django_db
class TestListingSpatialQueries:
    def test_dwithin_finds_nearby(self, kretinga_point):
        baker.make(
            Listing,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga centre",
        )
        baker.make(
            Listing,
            location=Point(21.2550, 55.8900, srid=4326),
            address_raw="Kretinga north",
        )
        results = Listing.objects.filter(location__dwithin=(kretinga_point, D(km=5)))
        assert results.count() == 2

    def test_dwithin_excludes_far(self, kretinga_point):
        baker.make(
            Listing,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Kretinga centre",
        )
        baker.make(
            Listing,
            location=Point(25.2797, 54.6872, srid=4326),
            address_raw="Vilnius centre",
        )
        results = Listing.objects.filter(location__dwithin=(kretinga_point, D(km=5)))
        assert results.count() == 1

    def test_distance_annotation(self, kretinga_point):
        from django.contrib.gis.db.models.functions import Distance

        baker.make(
            Listing,
            location=Point(21.2550, 55.8900, srid=4326),
            address_raw="1km away",
        )
        result = (
            Listing.objects.annotate(distance=Distance("location", kretinga_point))
            .order_by("distance")
            .first()
        )
        assert result.distance.m < 2000


@pytest.mark.django_db
class TestListingFiltering:
    def test_filter_by_property_type(self):
        baker.make(
            Listing,
            property_type=Listing.PropertyType.HOUSE,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="House",
        )
        baker.make(
            Listing,
            property_type=Listing.PropertyType.FLAT,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Flat",
        )
        houses = Listing.objects.filter(property_type=Listing.PropertyType.HOUSE)
        assert houses.count() == 1

    def test_filter_active_only(self):
        baker.make(
            Listing,
            is_active=True,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Active",
        )
        baker.make(
            Listing,
            is_active=False,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Inactive",
        )
        active = Listing.objects.filter(is_active=True)
        assert active.count() == 1

    def test_filter_new_construction(self):
        baker.make(
            Listing,
            is_new_construction=True,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="New",
        )
        baker.make(
            Listing,
            is_new_construction=False,
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Old",
        )
        new_only = Listing.objects.filter(is_new_construction=True)
        assert new_only.count() == 1

    def test_filter_by_price_range(self):
        baker.make(
            Listing,
            price=Decimal("100000"),
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Cheap",
        )
        baker.make(
            Listing,
            price=Decimal("300000"),
            location=Point(21.2420, 55.8835, srid=4326),
            address_raw="Expensive",
        )
        in_range = Listing.objects.filter(price__gte=150000, price__lte=250000)
        assert in_range.count() == 0
        all_range = Listing.objects.filter(price__gte=50000, price__lte=350000)
        assert all_range.count() == 2
