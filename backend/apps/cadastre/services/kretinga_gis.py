"""Client for Kretinga municipality ArcGIS REST services.

Portal: https://gis.kretinga.lt
Services: https://gis.kretinga.lt/arcgis/rest/services

Available layers:
- Kretingos_r_sav_sklypai (MapServer) — local cadastral plots
- Bendrieji_planai (MapServer) — general/master plans
- Specialieji_planai (MapServer) — special plans
- Kretingos_miesto_funkcinės_zonos_Map_Image — city zoning
- Kretingos_rajono_funkcinės_zonos_Map_Image — district zoning
- Inzineriniai_tinklai (MapServer) — utilities/engineering networks
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://gis.kretinga.lt/arcgis/rest/services"


def query_feature_layer(
    service_name: str,
    layer_index: int = 0,
    bbox: tuple[float, float, float, float] | None = None,
    where: str = "1=1",
    out_fields: str = "*",
    max_records: int = 1000,
) -> list[dict]:
    """Query an ArcGIS FeatureServer/MapServer layer.

    Args:
        service_name: e.g. "Kretingos_r_sav_sklypai"
        layer_index: layer number within the service (usually 0)
        bbox: (min_lng, min_lat, max_lng, max_lat) in EPSG:4326
        where: SQL where clause
        out_fields: comma-separated field names or "*"
        max_records: max features to return
    """
    url = f"{BASE_URL}/{service_name}/MapServer/{layer_index}/query"

    params: dict = {
        "where": where,
        "outFields": out_fields,
        "outSR": "4326",
        "f": "geojson",
        "returnGeometry": "true",
    }

    if bbox:
        min_lng, min_lat, max_lng, max_lat = bbox
        params["geometry"] = f"{min_lng},{min_lat},{max_lng},{max_lat}"
        params["geometryType"] = "esriGeometryEnvelope"
        params["inSR"] = "4326"
        params["spatialRel"] = "esriSpatialRelIntersects"

    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            logger.error(
                "ArcGIS error for %s: %s",
                service_name,
                data["error"].get("message", "Unknown"),
            )
            return []

        return data.get("features", [])

    except requests.RequestException:
        logger.exception("Failed to query Kretinga GIS: %s", service_name)
        return []


def fetch_kretinga_plots(
    bbox: tuple[float, float, float, float],
) -> list[dict]:
    """Fetch cadastral plots from Kretinga's local GIS."""
    return query_feature_layer("Kretingos_r_sav_sklypai", bbox=bbox)


def fetch_kretinga_zoning(
    bbox: tuple[float, float, float, float],
) -> list[dict]:
    """Fetch functional zoning data for Kretinga city."""
    return query_feature_layer(
        "Kretingos_miesto_funkcinės_zonos_Map_Image",
        bbox=bbox,
    )


def fetch_kretinga_plans(
    bbox: tuple[float, float, float, float],
) -> list[dict]:
    """Fetch general plans from Kretinga GIS."""
    return query_feature_layer("Bendrieji_planai", bbox=bbox)


def fetch_kretinga_utilities(
    bbox: tuple[float, float, float, float],
) -> list[dict]:
    """Fetch engineering network data (utilities) for Kretinga."""
    return query_feature_layer("Inzineriniai_tinklai", bbox=bbox)


def get_service_info(service_name: str) -> dict | None:
    """Get metadata about a Kretinga GIS service."""
    url = f"{BASE_URL}/{service_name}/MapServer?f=json"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        logger.exception("Failed to get service info: %s", service_name)
        return None
