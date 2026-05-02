import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.db import IntegrityError
from model_bakery import baker

from apps.documents.models import Document
from apps.planning.models import PlanningDocument


def _make_multipolygon(
    min_lng: float = 25.25,
    min_lat: float = 54.68,
    max_lng: float = 25.30,
    max_lat: float = 54.70,
) -> MultiPolygon:
    """Create a simple rectangular MultiPolygon for testing."""
    poly = Polygon.from_bbox((min_lng, min_lat, max_lng, max_lat))
    return MultiPolygon(poly, srid=4326)


@pytest.mark.django_db
class TestPlanningDocument:
    def test_create_planning_document(self):
        doc = baker.make(
            PlanningDocument,
            tpdris_id="TPD-2024-001",
            title="Vilniaus m. centrinės dalies detalusis planas",
            doc_type="detailed",
            status="approved",
            municipality="Vilniaus m. sav.",
            organizer="Vilniaus miesto savivaldybė",
            boundary=_make_multipolygon(),
            source_url="https://www.tpdris.lt/doc/TPD-2024-001",
        )
        assert doc.pk is not None
        assert "TPD-2024-001" in str(doc)
        assert "Vilniaus m. centrinės dalies" in str(doc)

    def test_unique_tpdris_id(self):
        baker.make(
            PlanningDocument,
            tpdris_id="TPD-UNIQUE-001",
            title="Plan A",
            doc_type="master",
            status="approved",
            municipality="Test",
            source_url="https://example.com/a",
        )
        with pytest.raises(IntegrityError):
            baker.make(
                PlanningDocument,
                tpdris_id="TPD-UNIQUE-001",
                title="Plan B",
                doc_type="detailed",
                status="preparation",
                municipality="Test",
                source_url="https://example.com/b",
            )

    def test_boundary_nullable(self):
        doc = baker.make(
            PlanningDocument,
            tpdris_id="TPD-NULL-BOUNDARY",
            title="Plan without boundary",
            doc_type="special",
            status="preparation",
            municipality="Kretingos r. sav.",
            boundary=None,
            source_url="https://example.com/c",
        )
        assert doc.boundary is None

    def test_spatial_query_intersects(self):
        baker.make(
            PlanningDocument,
            tpdris_id="TPD-VILNIUS",
            title="Vilnius plan",
            doc_type="detailed",
            status="approved",
            municipality="Vilniaus m. sav.",
            boundary=_make_multipolygon(25.25, 54.68, 25.30, 54.70),
            source_url="https://example.com/v",
        )
        baker.make(
            PlanningDocument,
            tpdris_id="TPD-KRETINGA",
            title="Kretinga plan",
            doc_type="master",
            status="approved",
            municipality="Kretingos r. sav.",
            boundary=_make_multipolygon(21.20, 55.85, 21.30, 55.90),
            source_url="https://example.com/k",
        )

        search_poly = Polygon.from_bbox((25.20, 54.65, 25.35, 54.75))
        results = PlanningDocument.objects.filter(boundary__intersects=search_poly)
        assert results.count() == 1
        assert results.first().tpdris_id == "TPD-VILNIUS"

    def test_llm_extraction_fields(self):
        doc = baker.make(
            PlanningDocument,
            tpdris_id="TPD-EXTRACT-001",
            title="Plan with extracted data",
            doc_type="detailed",
            status="approved",
            municipality="Vilniaus m. sav.",
            allowed_uses=["residential", "commercial"],
            max_height_m=12.0,
            max_floors=3,
            max_density=0.4,
            parking_requirements="1 vieta / 60 kv.m",
            extraction_confidence=0.85,
            source_url="https://example.com/e",
        )
        assert doc.allowed_uses == ["residential", "commercial"]
        assert doc.max_height_m == 12.0
        assert doc.max_floors == 3
        assert doc.max_density == 0.4
        assert doc.extraction_confidence == 0.85

    def test_documents_m2m(self):
        planning_doc = baker.make(
            PlanningDocument,
            tpdris_id="TPD-M2M-001",
            title="Plan with documents",
            doc_type="master",
            status="approved",
            municipality="Test",
            source_url="https://example.com/m",
        )
        doc1 = baker.make(
            Document,
            url="https://example.com/pdf1.pdf",
            title="PDF 1",
        )
        doc2 = baker.make(
            Document,
            url="https://example.com/pdf2.pdf",
            title="PDF 2",
        )
        planning_doc.documents.add(doc1, doc2)

        assert planning_doc.documents.count() == 2
        assert set(planning_doc.documents.values_list("title", flat=True)) == {"PDF 1", "PDF 2"}

    def test_doc_type_choices(self):
        field = PlanningDocument._meta.get_field("doc_type")
        choice_keys = [c[0] for c in field.choices]
        assert "master" in choice_keys
        assert "detailed" in choice_keys
        assert "special" in choice_keys

    def test_status_choices(self):
        field = PlanningDocument._meta.get_field("status")
        choice_keys = [c[0] for c in field.choices]
        assert "preparation" in choice_keys
        assert "public_review" in choice_keys
        assert "approved" in choice_keys
        assert "rejected" in choice_keys
        assert "expired" in choice_keys
