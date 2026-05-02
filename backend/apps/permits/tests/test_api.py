from datetime import date

import pytest
from django.contrib.gis.geos import Point
from django.test import Client
from model_bakery import baker

from apps.permits.models import BuildingPermit


@pytest.mark.django_db
class TestPermitsEndpoint:
    def test_list_permits_returns_json(self):
        baker.make(
            BuildingPermit,
            permit_number="LSNS-01-24-0001",
            permit_type="Statybos leidimas",
            status="issued",
            issued_at=date(2024, 3, 15),
            applicant_name="UAB Statybininkai",
            address_raw="Gedimino pr. 1, Vilnius",
            location=Point(25.28, 54.69, srid=4326),
            project_type="new",
            building_purpose="residential",
            source_url="https://planuojustatyti.lt/123",
        )
        client = Client()
        resp = client.get("/api/permits/", {"bbox": "25.20,54.65,25.35,54.75"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        permit = data[0]
        assert permit["permit_number"] == "LSNS-01-24-0001"
        assert permit["status"] == "issued"
        assert permit["applicant_name"] == "UAB Statybininkai"
        assert permit["lat"] == pytest.approx(54.69, abs=0.01)
        assert permit["lng"] == pytest.approx(25.28, abs=0.01)

    def test_list_permits_empty_bbox(self):
        baker.make(
            BuildingPermit,
            permit_number="FAR-001",
            location=Point(21.0, 55.0, srid=4326),
        )
        client = Client()
        resp = client.get("/api/permits/", {"bbox": "25.20,54.65,25.35,54.75"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 0

    def test_list_permits_filter_by_status(self):
        baker.make(
            BuildingPermit,
            permit_number="ISS-001",
            status="issued",
            location=Point(25.28, 54.69, srid=4326),
        )
        baker.make(
            BuildingPermit,
            permit_number="IP-001",
            status="in_progress",
            location=Point(25.28, 54.69, srid=4326),
        )
        client = Client()
        resp = client.get("/api/permits/", {"bbox": "25.20,54.65,25.35,54.75", "status": "issued"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["permit_number"] == "ISS-001"

    def test_list_permits_filter_by_issued_after(self):
        baker.make(
            BuildingPermit,
            permit_number="OLD-001",
            issued_at=date(2023, 1, 1),
            location=Point(25.28, 54.69, srid=4326),
        )
        baker.make(
            BuildingPermit,
            permit_number="NEW-001",
            issued_at=date(2024, 6, 15),
            location=Point(25.28, 54.69, srid=4326),
        )
        client = Client()
        resp = client.get(
            "/api/permits/",
            {"bbox": "25.20,54.65,25.35,54.75", "issued_after": "2024-01-01"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["permit_number"] == "NEW-001"

    def test_list_permits_invalid_bbox(self):
        client = Client()
        resp = client.get("/api/permits/", {"bbox": "invalid"})
        assert resp.status_code == 400

    def test_permits_without_location_excluded(self):
        """Permits without a location should not appear in bbox queries."""
        baker.make(
            BuildingPermit,
            permit_number="NO-LOC-001",
            location=None,
        )
        client = Client()
        resp = client.get("/api/permits/", {"bbox": "25.20,54.65,25.35,54.75"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 0

    def test_list_permits_combined_filters(self):
        """Test that bbox + status + issued_after can be combined."""
        baker.make(
            BuildingPermit,
            permit_number="MATCH-001",
            status="issued",
            issued_at=date(2024, 6, 1),
            location=Point(25.28, 54.69, srid=4326),
        )
        baker.make(
            BuildingPermit,
            permit_number="NO-MATCH-STATUS",
            status="in_progress",
            issued_at=date(2024, 6, 1),
            location=Point(25.28, 54.69, srid=4326),
        )
        baker.make(
            BuildingPermit,
            permit_number="NO-MATCH-DATE",
            status="issued",
            issued_at=date(2023, 1, 1),
            location=Point(25.28, 54.69, srid=4326),
        )
        client = Client()
        resp = client.get(
            "/api/permits/",
            {
                "bbox": "25.20,54.65,25.35,54.75",
                "status": "issued",
                "issued_after": "2024-01-01",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["permit_number"] == "MATCH-001"
