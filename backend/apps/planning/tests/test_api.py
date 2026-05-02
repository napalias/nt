import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.test import Client
from model_bakery import baker

from apps.planning.models import PlanningDocument


def _make_multipolygon(
    min_lng: float = 25.25,
    min_lat: float = 54.68,
    max_lng: float = 25.30,
    max_lat: float = 54.70,
) -> MultiPolygon:
    poly = Polygon.from_bbox((min_lng, min_lat, max_lng, max_lat))
    return MultiPolygon(poly, srid=4326)


@pytest.mark.django_db
class TestPlanningEndpoint:
    def test_list_planning_documents_returns_results(self):
        baker.make(
            PlanningDocument,
            tpdris_id="TPD-API-001",
            title="Vilnius detailed plan",
            doc_type="detailed",
            status="approved",
            municipality="Vilniaus m. sav.",
            boundary=_make_multipolygon(),
            allowed_uses=["residential", "commercial"],
            max_floors=3,
            max_height_m=12.0,
            extraction_confidence=0.85,
            source_url="https://www.tpdris.lt/doc/001",
        )
        client = Client()
        resp = client.get("/api/planning/", {"bbox": "25.20,54.65,25.35,54.75"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        doc = data[0]
        assert doc["tpdris_id"] == "TPD-API-001"
        assert doc["title"] == "Vilnius detailed plan"
        assert doc["doc_type"] == "detailed"
        assert doc["status"] == "approved"
        assert doc["allowed_uses"] == ["residential", "commercial"]
        assert doc["max_floors"] == 3
        assert doc["max_height_m"] == 12.0
        assert doc["extraction_confidence"] == 0.85
        assert doc["source_url"] == "https://www.tpdris.lt/doc/001"
        assert doc["boundary"] is not None

    def test_empty_bbox_no_results(self):
        baker.make(
            PlanningDocument,
            tpdris_id="TPD-API-FAR",
            title="Far away plan",
            doc_type="master",
            status="approved",
            municipality="Kretingos r. sav.",
            boundary=_make_multipolygon(21.20, 55.85, 21.30, 55.90),
            source_url="https://example.com/far",
        )
        client = Client()
        resp = client.get("/api/planning/", {"bbox": "25.20,54.65,25.35,54.75"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 0

    def test_excludes_null_boundary(self):
        baker.make(
            PlanningDocument,
            tpdris_id="TPD-NO-BOUNDARY",
            title="Plan without boundary",
            doc_type="special",
            status="approved",
            municipality="Test",
            boundary=None,
            source_url="https://example.com/nb",
        )
        client = Client()
        resp = client.get("/api/planning/", {"bbox": "25.20,54.65,25.35,54.75"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 0

    def test_invalid_bbox_returns_400(self):
        client = Client()
        resp = client.get("/api/planning/", {"bbox": "invalid"})
        assert resp.status_code == 400

    def test_too_few_bbox_values(self):
        client = Client()
        resp = client.get("/api/planning/", {"bbox": "25.20,54.65,25.35"})
        assert resp.status_code == 400

    def test_multiple_documents_in_bbox(self):
        baker.make(
            PlanningDocument,
            tpdris_id="TPD-MULTI-1",
            title="Plan 1",
            doc_type="detailed",
            status="approved",
            municipality="Vilniaus m. sav.",
            boundary=_make_multipolygon(25.26, 54.69, 25.28, 54.70),
            source_url="https://example.com/1",
        )
        baker.make(
            PlanningDocument,
            tpdris_id="TPD-MULTI-2",
            title="Plan 2",
            doc_type="master",
            status="approved",
            municipality="Vilniaus m. sav.",
            boundary=_make_multipolygon(25.27, 54.68, 25.29, 54.69),
            source_url="https://example.com/2",
        )
        client = Client()
        resp = client.get("/api/planning/", {"bbox": "25.20,54.65,25.35,54.75"})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
