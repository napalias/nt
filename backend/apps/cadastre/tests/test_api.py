from unittest.mock import patch

import pytest
from django.contrib.gis.geos import MultiPolygon, Point, Polygon
from django.test import Client
from model_bakery import baker

from apps.cadastre.api import parse_bbox
from apps.cadastre.models import CadastralPlot, HeritageObject, SpecialLandUseCondition


def _make_multipolygon(
    min_lng: float = 25.25, min_lat: float = 54.68, max_lng: float = 25.30, max_lat: float = 54.70
) -> MultiPolygon:
    poly = Polygon.from_bbox((min_lng, min_lat, max_lng, max_lat))
    return MultiPolygon(poly, srid=4326)


class TestParseBbox:
    def test_valid_bbox(self):
        result = parse_bbox("25.25,54.68,25.30,54.70")
        assert result == (25.25, 54.68, 25.30, 54.70)

    def test_bbox_with_spaces(self):
        result = parse_bbox("25.25 , 54.68 , 25.30 , 54.70")
        assert result == (25.25, 54.68, 25.30, 54.70)

    def test_bbox_too_few_values(self):
        with pytest.raises(ValueError, match="exactly 4 values"):
            parse_bbox("25.25,54.68,25.30")

    def test_bbox_too_many_values(self):
        with pytest.raises(ValueError, match="exactly 4 values"):
            parse_bbox("25.25,54.68,25.30,54.70,0")

    def test_bbox_invalid_float(self):
        with pytest.raises(ValueError):
            parse_bbox("abc,54.68,25.30,54.70")

    def test_bbox_empty_string(self):
        with pytest.raises(ValueError):
            parse_bbox("")


@pytest.mark.django_db
class TestCadastreEndpoint:
    @patch("apps.cadastre.api.sync_cadastre_for_bbox")
    def test_get_cadastral_plots_returns_geojson(self, mock_sync):
        baker.make(
            CadastralPlot,
            cadastral_number="0101/0001:1234",
            geometry=_make_multipolygon(),
            area_sqm=500.0,
            purpose="Gyvenamoji teritorija",
            purpose_category="residential",
            municipality="Vilniaus m. sav.",
        )
        client = Client()
        resp = client.get("/api/layers/cadastre", {"bbox": "25.20,54.65,25.35,54.75"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 1
        feature = data["features"][0]
        assert feature["type"] == "Feature"
        assert feature["properties"]["cadastral_number"] == "0101/0001:1234"
        assert feature["properties"]["area_sqm"] == 500.0
        assert feature["geometry"]["type"] in ("MultiPolygon", "Polygon")

    @patch("apps.cadastre.api.sync_cadastre_for_bbox")
    def test_get_cadastral_plots_empty_bbox(self, mock_sync):
        """Returns empty FeatureCollection when no data in bbox."""
        client = Client()
        resp = client.get("/api/layers/cadastre", {"bbox": "10.0,50.0,10.1,50.1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 0

    def test_get_cadastral_plots_invalid_bbox(self):
        client = Client()
        resp = client.get("/api/layers/cadastre", {"bbox": "invalid"})
        assert resp.status_code == 400

    @patch("apps.cadastre.api.sync_cadastre_for_bbox")
    def test_triggers_sync_when_no_data(self, mock_sync):
        """Should trigger async sync when no data exists for the bbox."""
        client = Client()
        client.get("/api/layers/cadastre", {"bbox": "25.20,54.65,25.35,54.75"})
        mock_sync.delay.assert_called_once_with(25.20, 54.65, 25.35, 54.75)

    @patch("apps.cadastre.api.sync_cadastre_for_bbox")
    def test_does_not_trigger_sync_when_data_exists(self, mock_sync):
        """Should not trigger sync when data already exists for the bbox."""
        baker.make(
            CadastralPlot,
            cadastral_number="EXIST-001",
            geometry=_make_multipolygon(),
            area_sqm=100.0,
        )
        client = Client()
        client.get("/api/layers/cadastre", {"bbox": "25.20,54.65,25.35,54.75"})
        mock_sync.delay.assert_not_called()


@pytest.mark.django_db
class TestHeritageEndpoint:
    @patch("apps.cadastre.api.sync_cadastre_for_bbox")
    def test_get_heritage_objects_returns_geojson(self, mock_sync):
        baker.make(
            HeritageObject,
            kvr_code="12345",
            name="Vilniaus senamiestis",
            category="Urbanistinis",
            protection_level="Valstybės saugomas",
            geometry=Point(25.28, 54.69, srid=4326),
        )
        client = Client()
        resp = client.get("/api/layers/heritage", {"bbox": "25.20,54.65,25.35,54.75"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 1
        feature = data["features"][0]
        assert feature["properties"]["kvr_code"] == "12345"
        assert feature["properties"]["name"] == "Vilniaus senamiestis"

    def test_get_heritage_invalid_bbox(self):
        client = Client()
        resp = client.get("/api/layers/heritage", {"bbox": "bad"})
        assert resp.status_code == 400


@pytest.mark.django_db
class TestRestrictionsEndpoint:
    @patch("apps.cadastre.api.sync_cadastre_for_bbox")
    def test_get_restrictions_returns_geojson(self, mock_sync):
        baker.make(
            SpecialLandUseCondition,
            category="Vandens apsaugos zona",
            geometry=_make_multipolygon(),
            description="50m nuo upės",
        )
        client = Client()
        resp = client.get("/api/layers/restrictions", {"bbox": "25.20,54.65,25.35,54.75"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 1
        feature = data["features"][0]
        assert feature["properties"]["category"] == "Vandens apsaugos zona"
        assert feature["properties"]["description"] == "50m nuo upės"

    def test_get_restrictions_invalid_bbox(self):
        client = Client()
        resp = client.get("/api/layers/restrictions", {"bbox": "x,y,z"})
        assert resp.status_code == 400
