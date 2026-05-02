from datetime import date

import pytest
from django.contrib.gis.geos import Point
from django.test import Client
from model_bakery import baker

from apps.developers.models import Developer


@pytest.fixture
def api_client():
    return Client()


@pytest.fixture
def vilnius_developers():
    """3 developers in Vilnius, 1 in Klaipeda (outside typical bbox)."""
    return [
        baker.make(
            Developer,
            company_code="300000001",
            name="UAB Statybos meistras",
            nace_codes=["41.10"],
            registered_address="Gedimino pr. 1, Vilnius",
            registered_address_point=Point(25.2797, 54.6872, srid=4326),
            founded=date(2010, 3, 15),
            status="active",
            employee_count=50,
        ),
        baker.make(
            Developer,
            company_code="300000002",
            name="UAB NT Projektai",
            nace_codes=["68.10", "68.20"],
            registered_address="Konstitucijos pr. 10, Vilnius",
            registered_address_point=Point(25.2650, 54.6950, srid=4326),
            founded=date(2015, 7, 1),
            status="active",
            employee_count=20,
        ),
        baker.make(
            Developer,
            company_code="300000003",
            name="UAB Senoji statyba",
            nace_codes=["41.20"],
            registered_address="Šeimyniškių g. 5, Vilnius",
            registered_address_point=Point(25.2800, 54.7000, srid=4326),
            founded=date(2005, 1, 10),
            status="liquidated",
            employee_count=0,
        ),
        baker.make(
            Developer,
            company_code="300000004",
            name="UAB Klaipėdos statyba",
            nace_codes=["41.10"],
            registered_address="H. Manto g. 20, Klaipėda",
            registered_address_point=Point(21.1443, 55.7033, srid=4326),
            founded=date(2018, 11, 20),
            status="active",
            employee_count=15,
        ),
    ]


@pytest.mark.django_db
class TestListDevelopersEndpoint:
    def test_list_by_bbox(self, api_client, vilnius_developers):
        # Vilnius area bbox
        resp = api_client.get("/api/developers/?bbox=25.20,54.65,25.35,54.75")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        names = {d["name"] for d in data}
        assert "UAB Klaipėdos statyba" not in names

    def test_excludes_outside_bbox(self, api_client, vilnius_developers):
        # Small bbox around Klaipeda only
        resp = api_client.get("/api/developers/?bbox=21.10,55.68,21.18,55.72")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["company_code"] == "300000004"

    def test_empty_bbox_returns_empty(self, api_client, vilnius_developers):
        # Bbox in the middle of the Baltic Sea
        resp = api_client.get("/api/developers/?bbox=19.0,56.0,19.1,56.1")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_invalid_bbox_returns_empty(self, api_client):
        resp = api_client.get("/api/developers/?bbox=invalid")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_response_shape(self, api_client, vilnius_developers):
        resp = api_client.get("/api/developers/?bbox=25.20,54.65,25.35,54.75")
        data = resp.json()
        d = data[0]
        assert "id" in d
        assert "company_code" in d
        assert "name" in d
        assert "nace_codes" in d
        assert "registered_address" in d
        assert "lat" in d
        assert "lng" in d
        assert "founded" in d
        assert "status" in d
        assert "employee_count" in d

    def test_developers_without_point_excluded(self, api_client):
        baker.make(
            Developer,
            company_code="300099999",
            name="UAB No Location",
            registered_address_point=None,
            status="active",
        )
        resp = api_client.get("/api/developers/?bbox=25.20,54.65,25.35,54.75")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.django_db
class TestGetDeveloperEndpoint:
    def test_get_by_id(self, api_client, vilnius_developers):
        dev = vilnius_developers[0]
        resp = api_client.get(f"/api/developers/{dev.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["company_code"] == "300000001"
        assert data["name"] == "UAB Statybos meistras"
        assert data["nace_codes"] == ["41.10"]
        assert data["lat"] == pytest.approx(54.6872, abs=0.001)
        assert data["lng"] == pytest.approx(25.2797, abs=0.001)
        assert data["founded"] == "2010-03-15"
        assert data["employee_count"] == 50

    def test_get_nonexistent_returns_404(self, api_client):
        resp = api_client.get("/api/developers/99999")
        assert resp.status_code == 404
