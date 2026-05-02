import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon
from django.db import IntegrityError
from model_bakery import baker

from apps.cadastre.models import CadastralPlot, HeritageObject, SpecialLandUseCondition


def _make_multipolygon(
    min_lng: float = 25.25, min_lat: float = 54.68, max_lng: float = 25.30, max_lat: float = 54.70
) -> MultiPolygon:
    """Create a simple rectangular MultiPolygon for testing."""
    poly = Polygon.from_bbox((min_lng, min_lat, max_lng, max_lat))
    return MultiPolygon(poly, srid=4326)


@pytest.mark.django_db
class TestCadastralPlot:
    def test_create_plot(self):
        plot = baker.make(
            CadastralPlot,
            cadastral_number="0101/0001:1234",
            geometry=_make_multipolygon(),
            area_sqm=500.0,
            purpose="Gyvenamoji teritorija",
            purpose_category="residential",
            municipality="Vilniaus m. sav.",
        )
        assert plot.pk is not None
        assert str(plot) == "0101/0001:1234 (Vilniaus m. sav.)"

    def test_unique_cadastral_number(self):
        baker.make(
            CadastralPlot,
            cadastral_number="0101/0001:1234",
            geometry=_make_multipolygon(),
            area_sqm=500.0,
        )
        with pytest.raises(IntegrityError):
            baker.make(
                CadastralPlot,
                cadastral_number="0101/0001:1234",
                geometry=_make_multipolygon(),
                area_sqm=600.0,
            )

    def test_spatial_query_intersects(self):
        baker.make(
            CadastralPlot,
            cadastral_number="VILNIUS-001",
            geometry=_make_multipolygon(25.25, 54.68, 25.30, 54.70),
            area_sqm=500.0,
        )
        baker.make(
            CadastralPlot,
            cadastral_number="KRETINGA-001",
            geometry=_make_multipolygon(21.20, 55.85, 21.30, 55.90),
            area_sqm=800.0,
        )
        search_poly = Polygon.from_bbox((25.20, 54.65, 25.35, 54.75))
        results = CadastralPlot.objects.filter(geometry__intersects=search_poly)
        assert results.count() == 1
        assert results.first().cadastral_number == "VILNIUS-001"


@pytest.mark.django_db
class TestHeritageObject:
    def test_create_heritage_object(self):
        obj = baker.make(
            HeritageObject,
            kvr_code="12345",
            name="Vilniaus senamiestis",
            category="Urbanistinis",
            protection_level="Valstybės saugomas",
            geometry=Point(25.28, 54.69, srid=4326),
        )
        assert obj.pk is not None
        assert str(obj) == "12345 — Vilniaus senamiestis"

    def test_unique_kvr_code(self):
        baker.make(
            HeritageObject,
            kvr_code="12345",
            name="Object A",
            geometry=Point(25.28, 54.69, srid=4326),
        )
        with pytest.raises(IntegrityError):
            baker.make(
                HeritageObject,
                kvr_code="12345",
                name="Object B",
                geometry=Point(25.29, 54.70, srid=4326),
            )

    def test_heritage_point_geometry(self):
        """Heritage objects can have Point geometry (GeometryField accepts any geom type)."""
        obj = baker.make(
            HeritageObject,
            kvr_code="PT-001",
            name="Paminklas",
            geometry=Point(25.28, 54.69, srid=4326),
        )
        assert obj.geometry.geom_type == "Point"

    def test_heritage_polygon_geometry(self):
        """Heritage objects can also have Polygon geometry."""
        poly = Polygon.from_bbox((25.27, 54.68, 25.29, 54.70))
        poly.srid = 4326
        obj = baker.make(
            HeritageObject,
            kvr_code="PL-001",
            name="Saugoma zona",
            geometry=poly,
        )
        assert obj.geometry.geom_type == "Polygon"


@pytest.mark.django_db
class TestSpecialLandUseCondition:
    def test_create_restriction(self):
        cond = baker.make(
            SpecialLandUseCondition,
            category="Vandens apsaugos zona",
            geometry=_make_multipolygon(),
            description="50m nuo upės",
        )
        assert cond.pk is not None
        assert str(cond) == "Vandens apsaugos zona"

    def test_multiple_restrictions_same_category(self):
        """Multiple restrictions of the same category are allowed."""
        cond1 = baker.make(
            SpecialLandUseCondition,
            category="Vandens apsaugos zona",
            geometry=_make_multipolygon(25.25, 54.68, 25.30, 54.70),
        )
        cond2 = baker.make(
            SpecialLandUseCondition,
            category="Vandens apsaugos zona",
            geometry=_make_multipolygon(21.20, 55.85, 21.30, 55.90),
        )
        assert cond1.pk != cond2.pk
        assert SpecialLandUseCondition.objects.filter(category="Vandens apsaugos zona").count() == 2
