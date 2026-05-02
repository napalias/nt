from __future__ import annotations

import logging

from django.contrib.gis.geos import Polygon
from ninja import Router, Schema

from apps.cadastre.models import CadastralPlot, HeritageObject, SpecialLandUseCondition
from apps.cadastre.tasks import sync_cadastre_for_bbox

logger = logging.getLogger(__name__)

router = Router(tags=["layers"])


def parse_bbox(bbox_str: str) -> tuple[float, float, float, float]:
    """Parse a bbox query parameter into (min_lng, min_lat, max_lng, max_lat).

    Args:
        bbox_str: Comma-separated string "min_lng,min_lat,max_lng,max_lat".

    Returns:
        Tuple of (min_lng, min_lat, max_lng, max_lat).

    Raises:
        ValueError: If the string cannot be parsed into exactly 4 floats.
    """
    parts = bbox_str.split(",")
    if len(parts) != 4:
        msg = f"bbox must have exactly 4 values, got {len(parts)}"
        raise ValueError(msg)
    min_lng, min_lat, max_lng, max_lat = (float(p.strip()) for p in parts)
    return min_lng, min_lat, max_lng, max_lat


def bbox_to_polygon(min_lng: float, min_lat: float, max_lng: float, max_lat: float) -> Polygon:
    """Convert bbox coordinates to a GEOS Polygon for spatial queries."""
    return Polygon.from_bbox((min_lng, min_lat, max_lng, max_lat))


def _trigger_sync_if_empty(bbox: tuple[float, float, float, float], model_class: type) -> None:
    """Trigger an async sync if no data exists for this bbox."""
    min_lng, min_lat, max_lng, max_lat = bbox
    poly = bbox_to_polygon(*bbox)
    if not model_class.objects.filter(geometry__intersects=poly).exists():
        sync_cadastre_for_bbox.delay(min_lng, min_lat, max_lng, max_lat)


# --- Schemas ---


class CadastralPlotFeature(Schema):
    type: str = "Feature"
    geometry: dict
    properties: dict


class CadastralPlotCollection(Schema):
    type: str = "FeatureCollection"
    features: list[CadastralPlotFeature]


class HeritageFeature(Schema):
    type: str = "Feature"
    geometry: dict
    properties: dict


class HeritageCollection(Schema):
    type: str = "FeatureCollection"
    features: list[HeritageFeature]


class RestrictionFeature(Schema):
    type: str = "Feature"
    geometry: dict
    properties: dict


class RestrictionCollection(Schema):
    type: str = "FeatureCollection"
    features: list[RestrictionFeature]


class ErrorOut(Schema):
    detail: str


# --- Endpoints ---


@router.get("/cadastre", response={200: CadastralPlotCollection, 400: ErrorOut})
def get_cadastral_plots(request, bbox: str):
    """Return GeoJSON of cadastral plots within the bounding box.

    Query parameter `bbox` format: min_lng,min_lat,max_lng,max_lat
    Triggers a background sync if no data exists for the requested area.
    """
    try:
        parsed = parse_bbox(bbox)
    except ValueError as e:
        return 400, {"detail": str(e)}

    poly = bbox_to_polygon(*parsed)
    _trigger_sync_if_empty(parsed, CadastralPlot)

    plots = CadastralPlot.objects.filter(geometry__intersects=poly)[:500]

    features = []
    for plot in plots:
        features.append(
            {
                "type": "Feature",
                "geometry": _geom_to_geojson(plot.geometry),
                "properties": {
                    "id": plot.id,
                    "cadastral_number": plot.cadastral_number,
                    "area_sqm": plot.area_sqm,
                    "purpose": plot.purpose,
                    "purpose_category": plot.purpose_category,
                    "municipality": plot.municipality,
                    "synced_at": plot.synced_at.isoformat(),
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}


@router.get("/heritage", response={200: HeritageCollection, 400: ErrorOut})
def get_heritage_objects(request, bbox: str):
    """Return GeoJSON of cultural heritage objects within the bounding box.

    Query parameter `bbox` format: min_lng,min_lat,max_lng,max_lat
    Triggers a background sync if no data exists for the requested area.
    """
    try:
        parsed = parse_bbox(bbox)
    except ValueError as e:
        return 400, {"detail": str(e)}

    poly = bbox_to_polygon(*parsed)
    _trigger_sync_if_empty(parsed, HeritageObject)

    objects = HeritageObject.objects.filter(geometry__intersects=poly)[:500]

    features = []
    for obj in objects:
        features.append(
            {
                "type": "Feature",
                "geometry": _geom_to_geojson(obj.geometry),
                "properties": {
                    "id": obj.id,
                    "kvr_code": obj.kvr_code,
                    "name": obj.name,
                    "category": obj.category,
                    "protection_level": obj.protection_level,
                    "synced_at": obj.synced_at.isoformat(),
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}


@router.get("/restrictions", response={200: RestrictionCollection, 400: ErrorOut})
def get_restrictions(request, bbox: str):
    """Return GeoJSON of special land use conditions within the bounding box.

    Query parameter `bbox` format: min_lng,min_lat,max_lng,max_lat
    Triggers a background sync if no data exists for the requested area.
    """
    try:
        parsed = parse_bbox(bbox)
    except ValueError as e:
        return 400, {"detail": str(e)}

    poly = bbox_to_polygon(*parsed)
    _trigger_sync_if_empty(parsed, SpecialLandUseCondition)

    conditions = SpecialLandUseCondition.objects.filter(geometry__intersects=poly)[:500]

    features = []
    for cond in conditions:
        features.append(
            {
                "type": "Feature",
                "geometry": _geom_to_geojson(cond.geometry),
                "properties": {
                    "id": cond.id,
                    "category": cond.category,
                    "description": cond.description,
                    "synced_at": cond.synced_at.isoformat(),
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}


@router.get("/kretinga/zoning")
def get_kretinga_zoning(request, bbox: str):
    """Return zoning data from Kretinga municipality GIS."""
    from apps.cadastre.services.kretinga_gis import fetch_kretinga_zoning

    try:
        parsed = parse_bbox(bbox)
    except ValueError as e:
        return {"error": str(e)}

    features = fetch_kretinga_zoning(parsed)
    return {"type": "FeatureCollection", "features": features}


@router.get("/kretinga/plots")
def get_kretinga_plots(request, bbox: str):
    """Return local cadastral plots from Kretinga municipality GIS."""
    from apps.cadastre.services.kretinga_gis import fetch_kretinga_plots

    try:
        parsed = parse_bbox(bbox)
    except ValueError as e:
        return {"error": str(e)}

    features = fetch_kretinga_plots(parsed)
    return {"type": "FeatureCollection", "features": features}


@router.get("/kretinga/utilities")
def get_kretinga_utilities(request, bbox: str):
    """Return engineering networks from Kretinga municipality GIS."""
    from apps.cadastre.services.kretinga_gis import fetch_kretinga_utilities

    try:
        parsed = parse_bbox(bbox)
    except ValueError as e:
        return {"error": str(e)}

    features = fetch_kretinga_utilities(parsed)
    return {"type": "FeatureCollection", "features": features}


def _geom_to_geojson(geom) -> dict:
    """Convert a GEOS geometry to a GeoJSON dict."""
    import json

    return json.loads(geom.geojson)
