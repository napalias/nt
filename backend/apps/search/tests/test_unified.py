from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon
from django.test import Client
from model_bakery import baker

from apps.cadastre.models import CadastralPlot, HeritageObject, SpecialLandUseCondition
from apps.developers.models import Developer
from apps.listings.models import Listing
from apps.permits.models import BuildingPermit
from apps.planning.models import PlanningDocument


@pytest.fixture
def api_client():
    return Client()


# --- Geometry helpers ---

KRETINGA_LNG, KRETINGA_LAT = 21.2420, 55.8835
VILNIUS_LNG, VILNIUS_LAT = 25.2797, 54.6872


def _make_small_polygon(lng: float, lat: float, size: float = 0.001) -> MultiPolygon:
    """Create a small MultiPolygon around a point."""
    poly = Polygon.from_bbox((lng - size, lat - size, lng + size, lat + size))
    mpoly = MultiPolygon(poly)
    mpoly.srid = 4326
    return mpoly


# --- Fixtures ---


@pytest.fixture
def kretinga_listing():
    return baker.make(
        Listing,
        title="Namas Kretingoje",
        price=Decimal("200000"),
        area_sqm=Decimal("100"),
        rooms=4,
        property_type=Listing.PropertyType.HOUSE,
        listing_type=Listing.ListingType.SALE,
        is_active=True,
        city="Kretinga",
        address_raw="Kretinga centras",
        location=Point(KRETINGA_LNG, KRETINGA_LAT, srid=4326),
    )


@pytest.fixture
def kretinga_permit(kretinga_developer):
    return baker.make(
        BuildingPermit,
        permit_number="LSNS-01-24-0001",
        applicant_name="UAB TestDev",
        applicant=kretinga_developer,
        status="issued",
        issued_at=date(2024, 6, 15),
        building_purpose="residential",
        project_type="new",
        cadastral_number="5530/0001:0001",
        location=Point(KRETINGA_LNG, KRETINGA_LAT, srid=4326),
        source_url="https://planuojustatyti.lt/test",
    )


@pytest.fixture
def kretinga_developer():
    return baker.make(
        Developer,
        company_code="300111222",
        name="UAB TestDev",
        nace_codes=["41.10"],
        registered_address="Kretinga, Test g. 1",
        registered_address_point=Point(KRETINGA_LNG, KRETINGA_LAT, srid=4326),
        status="active",
    )


@pytest.fixture
def kretinga_planning():
    return baker.make(
        PlanningDocument,
        tpdris_id="TP-001",
        title="Kretingos detalusis planas",
        doc_type="detailed",
        status="approved",
        municipality="Kretinga",
        max_floors=3,
        allowed_uses=["residential", "commercial"],
        boundary=_make_small_polygon(KRETINGA_LNG, KRETINGA_LAT),
        source_url="https://tpdris.lt/test",
    )


@pytest.fixture
def kretinga_plot():
    return baker.make(
        CadastralPlot,
        cadastral_number="5530/0001:0001",
        geometry=_make_small_polygon(KRETINGA_LNG, KRETINGA_LAT),
        area_sqm=500.0,
        purpose="Gyvenamoji teritorija",
        purpose_category="residential",
        municipality="Kretinga",
    )


@pytest.fixture
def kretinga_heritage():
    return baker.make(
        HeritageObject,
        kvr_code="KVR-001",
        name="Kretingos dvaro parkas",
        category="Kultūros paminklas",
        protection_level="valstybinis",
        geometry=_make_small_polygon(KRETINGA_LNG, KRETINGA_LAT),
    )


@pytest.fixture
def kretinga_slu():
    return baker.make(
        SpecialLandUseCondition,
        category="Vandens apsaugos zona (50m)",
        description="River protection zone",
        geometry=_make_small_polygon(KRETINGA_LNG, KRETINGA_LAT),
    )


# --- Full search tests ---


@pytest.mark.django_db
class TestFullSearch:
    def test_returns_all_layer_keys(self, api_client):
        """The multi-layer response must contain every expected top-level key."""
        resp = api_client.get(
            f"/api/search/full?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "center" in data
        assert "radius_m" in data
        assert "listings" in data
        assert "permits" in data
        assert "developers" in data
        assert "planning" in data
        assert "restrictions" in data
        assert "heritage" in data["restrictions"]
        assert "special_land_use" in data["restrictions"]

    def test_empty_layers(self, api_client):
        """All layers should be empty lists when no data exists."""
        resp = api_client.get(
            f"/api/search/full?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000"
        )
        data = resp.json()
        assert data["listings"] == []
        assert data["permits"] == []
        assert data["developers"] == []
        assert data["planning"] == []
        assert data["restrictions"]["heritage"] == []
        assert data["restrictions"]["special_land_use"] == []

    def test_full_search_includes_listings(self, api_client, kretinga_listing):
        resp = api_client.get(
            f"/api/search/full?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000"
        )
        data = resp.json()
        assert len(data["listings"]) == 1
        assert data["listings"][0]["title"] == "Namas Kretingoje"

    def test_full_search_includes_permits(self, api_client, kretinga_permit):
        resp = api_client.get(
            f"/api/search/full?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000"
        )
        data = resp.json()
        assert len(data["permits"]) == 1
        assert data["permits"][0]["permit_number"] == "LSNS-01-24-0001"
        assert data["permits"][0]["status"] == "issued"

    def test_full_search_includes_developers(self, api_client, kretinga_developer):
        resp = api_client.get(
            f"/api/search/full?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000"
        )
        data = resp.json()
        assert len(data["developers"]) == 1
        assert data["developers"][0]["name"] == "UAB TestDev"
        assert data["developers"][0]["company_code"] == "300111222"

    def test_full_search_includes_planning(self, api_client, kretinga_planning):
        resp = api_client.get(
            f"/api/search/full?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000"
        )
        data = resp.json()
        assert len(data["planning"]) == 1
        assert data["planning"][0]["doc_type"] == "detailed"
        assert data["planning"][0]["max_floors"] == 3
        assert data["planning"][0]["allowed_uses"] == ["residential", "commercial"]

    def test_full_search_includes_restrictions(self, api_client, kretinga_heritage, kretinga_slu):
        resp = api_client.get(
            f"/api/search/full?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000"
        )
        data = resp.json()
        assert len(data["restrictions"]["heritage"]) == 1
        assert data["restrictions"]["heritage"][0]["name"] == "Kretingos dvaro parkas"
        assert len(data["restrictions"]["special_land_use"]) == 1
        assert "Vandens" in data["restrictions"]["special_land_use"][0]["category"]

    def test_full_search_excludes_distant_data(self, api_client, kretinga_listing):
        """Data near Kretinga should not appear in a Vilnius search."""
        resp = api_client.get(f"/api/search/full?lat={VILNIUS_LAT}&lng={VILNIUS_LNG}&radius_m=5000")
        data = resp.json()
        assert data["listings"] == []

    def test_full_search_caps_radius(self, api_client):
        resp = api_client.get(
            f"/api/search/full?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=999999"
        )
        data = resp.json()
        assert data["radius_m"] == 50000

    def test_developer_active_permits_count(self, api_client, kretinga_developer, kretinga_permit):
        """Developer summary should include count of active permits."""
        resp = api_client.get(
            f"/api/search/full?lat={KRETINGA_LAT}&lng={KRETINGA_LNG}&radius_m=5000"
        )
        data = resp.json()
        dev = data["developers"][0]
        assert dev["active_permits_count"] == 1


# --- Property report tests ---


@pytest.mark.django_db
class TestPropertyReport:
    def test_property_report_not_found(self, api_client):
        resp = api_client.get("/api/property/9999/0000:0000")
        assert resp.status_code == 404
        data = resp.json()
        assert "not found" in data["detail"].lower()

    def test_property_report_basic(self, api_client, kretinga_plot):
        resp = api_client.get(f"/api/property/{kretinga_plot.cadastral_number}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["plot"]["cadastral_number"] == "5530/0001:0001"
        assert data["plot"]["area_sqm"] == 500.0
        assert data["plot"]["purpose_category"] == "residential"

    def test_property_report_includes_permits(self, api_client, kretinga_plot, kretinga_permit):
        resp = api_client.get(f"/api/property/{kretinga_plot.cadastral_number}")
        data = resp.json()
        assert len(data["permits"]) == 1
        assert data["permits"][0]["permit_number"] == "LSNS-01-24-0001"

    def test_property_report_includes_nearby_listings(
        self, api_client, kretinga_plot, kretinga_listing
    ):
        """Listings within 100m of the plot centroid should appear."""
        resp = api_client.get(f"/api/property/{kretinga_plot.cadastral_number}")
        data = resp.json()
        assert len(data["listings"]) == 1
        assert data["listings"][0]["title"] == "Namas Kretingoje"

    def test_property_report_excludes_distant_listings(self, api_client, kretinga_plot):
        """A listing far from the plot should not appear in the report."""
        baker.make(
            Listing,
            title="Namas Vilniuje",
            is_active=True,
            location=Point(VILNIUS_LNG, VILNIUS_LAT, srid=4326),
        )
        resp = api_client.get(f"/api/property/{kretinga_plot.cadastral_number}")
        data = resp.json()
        assert data["listings"] == []

    def test_property_report_includes_planning(self, api_client, kretinga_plot, kretinga_planning):
        resp = api_client.get(f"/api/property/{kretinga_plot.cadastral_number}")
        data = resp.json()
        assert len(data["planning"]) == 1
        assert data["planning"][0]["title"] == "Kretingos detalusis planas"

    def test_property_report_includes_restrictions(
        self, api_client, kretinga_plot, kretinga_heritage, kretinga_slu
    ):
        resp = api_client.get(f"/api/property/{kretinga_plot.cadastral_number}")
        data = resp.json()
        assert len(data["restrictions"]["heritage"]) == 1
        assert len(data["restrictions"]["special_land_use"]) == 1

    def test_property_report_includes_developers(self, api_client, kretinga_plot, kretinga_permit):
        """Developers linked via permits on this plot should appear."""
        resp = api_client.get(f"/api/property/{kretinga_plot.cadastral_number}")
        data = resp.json()
        assert len(data["developers"]) == 1
        assert data["developers"][0]["name"] == "UAB TestDev"

    def test_property_report_response_shape(self, api_client, kretinga_plot):
        """Verify all top-level keys are present even when layers are empty."""
        resp = api_client.get(f"/api/property/{kretinga_plot.cadastral_number}")
        data = resp.json()
        assert "plot" in data
        assert "listings" in data
        assert "permits" in data
        assert "planning" in data
        assert "developers" in data
        assert "restrictions" in data
        assert "heritage" in data["restrictions"]
        assert "special_land_use" in data["restrictions"]
