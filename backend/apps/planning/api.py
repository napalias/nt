from __future__ import annotations

import json
import logging

from django.contrib.gis.geos import Polygon
from ninja import Router, Schema

from apps.planning.models import PlanningDocument

logger = logging.getLogger(__name__)

router = Router(tags=["planning"])

MAX_RESULTS = 200


class PlanningDocumentOut(Schema):
    id: int
    tpdris_id: str
    title: str
    doc_type: str
    status: str
    municipality: str
    organizer: str
    approved_at: str | None
    expires_at: str | None
    boundary: dict | None
    allowed_uses: list
    max_height_m: float | None
    max_floors: int | None
    max_density: float | None
    parking_requirements: str
    extraction_confidence: float | None
    source_url: str
    scraped_at: str


class ErrorOut(Schema):
    detail: str


def _parse_bbox(bbox_str: str) -> tuple[float, float, float, float]:
    """Parse a bbox query parameter into (min_lng, min_lat, max_lng, max_lat)."""
    parts = bbox_str.split(",")
    if len(parts) != 4:
        msg = f"bbox must have exactly 4 values, got {len(parts)}"
        raise ValueError(msg)
    min_lng, min_lat, max_lng, max_lat = (float(p.strip()) for p in parts)
    return min_lng, min_lat, max_lng, max_lat


@router.get("/", response={200: list[PlanningDocumentOut], 400: ErrorOut})
def list_planning_documents(request, bbox: str):
    """Return planning documents whose boundary intersects the bounding box.

    Query parameter `bbox` format: min_lng,min_lat,max_lng,max_lat
    """
    try:
        min_lng, min_lat, max_lng, max_lat = _parse_bbox(bbox)
    except ValueError as e:
        return 400, {"detail": str(e)}

    bbox_polygon = Polygon.from_bbox((min_lng, min_lat, max_lng, max_lat))
    bbox_polygon.srid = 4326

    qs = PlanningDocument.objects.filter(boundary__intersects=bbox_polygon).order_by(
        "-approved_at"
    )[:MAX_RESULTS]

    return [_to_out(doc) for doc in qs]


def _to_out(doc: PlanningDocument) -> dict:
    return {
        "id": doc.id,
        "tpdris_id": doc.tpdris_id,
        "title": doc.title,
        "doc_type": doc.doc_type,
        "status": doc.status,
        "municipality": doc.municipality,
        "organizer": doc.organizer,
        "approved_at": doc.approved_at.isoformat() if doc.approved_at else None,
        "expires_at": doc.expires_at.isoformat() if doc.expires_at else None,
        "boundary": json.loads(doc.boundary.geojson) if doc.boundary else None,
        "allowed_uses": doc.allowed_uses or [],
        "max_height_m": doc.max_height_m,
        "max_floors": doc.max_floors,
        "max_density": doc.max_density,
        "parking_requirements": doc.parking_requirements,
        "extraction_confidence": doc.extraction_confidence,
        "source_url": doc.source_url,
        "scraped_at": doc.scraped_at.isoformat(),
    }
