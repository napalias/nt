"""GeoPortal WFS client for fetching cadastral, heritage, and restriction data.

Uses OWSLib to connect to Lithuanian GeoPortal WFS endpoints
and returns parsed Django model instances.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon
from django.utils import timezone
from owslib.wfs import WebFeatureService

if TYPE_CHECKING:
    from apps.cadastre.models import CadastralPlot, HeritageObject, SpecialLandUseCondition

logger = logging.getLogger(__name__)

# Bounding box type: (min_lng, min_lat, max_lng, max_lat)
BBox = tuple[float, float, float, float]


def _get_wfs(url: str | None = None, version: str = "2.0.0") -> WebFeatureService:
    """Create a WFS client for the given URL."""
    wfs_url = url or settings.GEOPORTAL_WFS_URL
    return WebFeatureService(url=wfs_url, version=version, timeout=30)


def _ensure_multipolygon(geom: GEOSGeometry) -> MultiPolygon:
    """Ensure geometry is a MultiPolygon. Wraps single Polygons."""
    if isinstance(geom, MultiPolygon):
        return geom
    if isinstance(geom, Polygon):
        return MultiPolygon(geom, srid=geom.srid)
    msg = f"Expected Polygon or MultiPolygon, got {geom.geom_type}"
    raise ValueError(msg)


def _parse_gml_geometry(feature: dict, geom_key: str = "geometry") -> GEOSGeometry | None:
    """Parse a GML geometry from a WFS feature into a GEOSGeometry."""
    geom_data = feature.get(geom_key)
    if geom_data is None:
        return None
    try:
        if isinstance(geom_data, str):
            return GEOSGeometry(geom_data, srid=4326)
        return GEOSGeometry(str(geom_data), srid=4326)
    except Exception:
        logger.warning("Failed to parse geometry for feature: %s", feature.get("id", "unknown"))
        return None


def fetch_cadastral_plots(bbox: BBox) -> list[CadastralPlot]:
    """Fetch cadastral plots from GeoPortal WFS within the given bounding box.

    Args:
        bbox: Bounding box as (min_lng, min_lat, max_lng, max_lat).

    Returns:
        List of unsaved CadastralPlot model instances.
    """
    from apps.cadastre.models import CadastralPlot

    layer_name = settings.GEOPORTAL_LAYER_CADASTRE
    now = timezone.now()
    plots: list[CadastralPlot] = []

    try:
        wfs = _get_wfs()
        response = wfs.getfeature(
            typename=[layer_name],
            bbox=bbox,
            srsname="EPSG:4326",
            maxfeatures=1000,
            outputFormat="application/json",
        )
        import json

        data = json.loads(response.read())
    except Exception:
        logger.exception("Failed to fetch cadastral plots from GeoPortal for bbox=%s", bbox)
        return []

    for feature in data.get("features", []):
        props = feature.get("properties", {})
        geom_data = feature.get("geometry")
        if not geom_data:
            continue

        try:
            geom = GEOSGeometry(json.dumps(geom_data), srid=4326)
            geom = _ensure_multipolygon(geom)
        except (ValueError, Exception):
            logger.warning(
                "Skipping plot with invalid geometry: %s",
                props.get("KADASTRO_NR", "unknown"),
            )
            continue

        cadastral_number = props.get("KADASTRO_NR", "") or props.get("unique_number", "")
        if not cadastral_number:
            continue

        plots.append(
            CadastralPlot(
                cadastral_number=cadastral_number,
                geometry=geom,
                area_sqm=float(props.get("PLOTAS", 0) or props.get("area", 0)),
                purpose=props.get("PASKIRTIS", "") or props.get("purpose", ""),
                purpose_category=props.get("PASKIRTIS_KATEGORIJA", "")
                or props.get("purpose_group", ""),
                municipality=props.get("SAVIVALDYBE", "") or props.get("municipality", ""),
                synced_at=now,
            )
        )

    logger.info("Fetched %d cadastral plots for bbox=%s", len(plots), bbox)
    return plots


def fetch_heritage_objects(bbox: BBox) -> list[HeritageObject]:
    """Fetch cultural heritage objects from GeoPortal WFS within the given bounding box.

    Args:
        bbox: Bounding box as (min_lng, min_lat, max_lng, max_lat).

    Returns:
        List of unsaved HeritageObject model instances.
    """
    from apps.cadastre.models import HeritageObject

    layer_name = settings.GEOPORTAL_LAYER_HERITAGE
    now = timezone.now()
    objects: list[HeritageObject] = []

    try:
        wfs = _get_wfs()
        response = wfs.getfeature(
            typename=[layer_name],
            bbox=bbox,
            srsname="EPSG:4326",
            maxfeatures=1000,
            outputFormat="application/json",
        )
        import json

        data = json.loads(response.read())
    except Exception:
        logger.exception("Failed to fetch heritage objects from GeoPortal for bbox=%s", bbox)
        return []

    for feature in data.get("features", []):
        props = feature.get("properties", {})
        geom_data = feature.get("geometry")
        if not geom_data:
            continue

        try:
            import json as json_mod

            geom = GEOSGeometry(json_mod.dumps(geom_data), srid=4326)
        except Exception:
            logger.warning(
                "Skipping heritage object with invalid geometry: %s",
                props.get("KVR_KODAS", "unknown"),
            )
            continue

        kvr_code = props.get("KVR_KODAS", "") or props.get("code", "")
        if not kvr_code:
            continue

        objects.append(
            HeritageObject(
                kvr_code=kvr_code,
                name=props.get("PAVADINIMAS", "") or props.get("name", ""),
                category=props.get("KATEGORIJA", "") or props.get("category", ""),
                protection_level=props.get("APSAUGOS_LYGIS", "")
                or props.get("protection_level", ""),
                geometry=geom,
                synced_at=now,
            )
        )

    logger.info("Fetched %d heritage objects for bbox=%s", len(objects), bbox)
    return objects


def fetch_restrictions(bbox: BBox) -> list[SpecialLandUseCondition]:
    """Fetch special land use conditions (SZNS) from GeoPortal WFS.

    Args:
        bbox: Bounding box as (min_lng, min_lat, max_lng, max_lat).

    Returns:
        List of unsaved SpecialLandUseCondition model instances.
    """
    from apps.cadastre.models import SpecialLandUseCondition

    layer_name = settings.GEOPORTAL_LAYER_RESTRICTIONS
    now = timezone.now()
    conditions: list[SpecialLandUseCondition] = []

    try:
        wfs = _get_wfs()
        response = wfs.getfeature(
            typename=[layer_name],
            bbox=bbox,
            srsname="EPSG:4326",
            maxfeatures=1000,
            outputFormat="application/json",
        )
        import json

        data = json.loads(response.read())
    except Exception:
        logger.exception("Failed to fetch restrictions from GeoPortal for bbox=%s", bbox)
        return []

    for feature in data.get("features", []):
        props = feature.get("properties", {})
        geom_data = feature.get("geometry")
        if not geom_data:
            continue

        try:
            import json as json_mod

            geom = GEOSGeometry(json_mod.dumps(geom_data), srid=4326)
            geom = _ensure_multipolygon(geom)
        except (ValueError, Exception):
            logger.warning("Skipping restriction with invalid geometry")
            continue

        conditions.append(
            SpecialLandUseCondition(
                category=props.get("KATEGORIJA", "") or props.get("category", ""),
                geometry=geom,
                description=props.get("APRASYMAS", "") or props.get("description", ""),
                synced_at=now,
            )
        )

    logger.info("Fetched %d restrictions for bbox=%s", len(conditions), bbox)
    return conditions
