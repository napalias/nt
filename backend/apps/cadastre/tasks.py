from __future__ import annotations

import logging

from celery import shared_task
from django.db import IntegrityError

from apps.cadastre.models import CadastralPlot, HeritageObject, SpecialLandUseCondition
from apps.cadastre.services.geoportal import (
    fetch_cadastral_plots,
    fetch_heritage_objects,
    fetch_restrictions,
)

logger = logging.getLogger(__name__)


@shared_task
def sync_cadastre_for_bbox(min_lng: float, min_lat: float, max_lng: float, max_lat: float) -> dict:
    """Fetch and upsert cadastral plots, heritage objects, and restrictions for a bbox.

    Strategy: lazy population. Called on demand when the user pans the map
    outside currently-cached areas. Fetches from GeoPortal WFS and upserts
    into the local database.

    Args:
        min_lng: Minimum longitude of bounding box.
        min_lat: Minimum latitude of bounding box.
        max_lng: Maximum longitude of bounding box.
        max_lat: Maximum latitude of bounding box.

    Returns:
        Summary dict with counts of synced records.
    """
    bbox = (min_lng, min_lat, max_lng, max_lat)
    result = {"plots": 0, "heritage": 0, "restrictions": 0}

    # --- Cadastral plots ---
    plots = fetch_cadastral_plots(bbox)
    for plot in plots:
        try:
            CadastralPlot.objects.update_or_create(
                cadastral_number=plot.cadastral_number,
                defaults={
                    "geometry": plot.geometry,
                    "area_sqm": plot.area_sqm,
                    "purpose": plot.purpose,
                    "purpose_category": plot.purpose_category,
                    "municipality": plot.municipality,
                    "synced_at": plot.synced_at,
                },
            )
            result["plots"] += 1
        except IntegrityError:
            logger.warning("Integrity error upserting plot %s", plot.cadastral_number)

    # --- Heritage objects ---
    heritage_objects = fetch_heritage_objects(bbox)
    for obj in heritage_objects:
        try:
            HeritageObject.objects.update_or_create(
                kvr_code=obj.kvr_code,
                defaults={
                    "name": obj.name,
                    "category": obj.category,
                    "protection_level": obj.protection_level,
                    "geometry": obj.geometry,
                    "synced_at": obj.synced_at,
                },
            )
            result["heritage"] += 1
        except IntegrityError:
            logger.warning("Integrity error upserting heritage object %s", obj.kvr_code)

    # --- Restrictions ---
    restrictions = fetch_restrictions(bbox)
    for cond in restrictions:
        try:
            # Restrictions don't have a unique natural key, so we create new ones
            # and rely on bbox-based deduplication at query time.
            SpecialLandUseCondition.objects.create(
                category=cond.category,
                geometry=cond.geometry,
                description=cond.description,
                synced_at=cond.synced_at,
            )
            result["restrictions"] += 1
        except IntegrityError:
            logger.warning("Integrity error creating restriction")

    logger.info("Sync complete for bbox=%s: %s", bbox, result)
    return result
