from __future__ import annotations

import logging

from django.contrib.gis.geos import Polygon
from ninja import Router, Schema

from apps.permits.models import BuildingPermit

logger = logging.getLogger(__name__)

router = Router(tags=["permits"])

MAX_RESULTS = 500


class PermitOut(Schema):
    id: int
    permit_number: str
    permit_type: str
    status: str
    issued_at: str | None
    applicant_name: str
    applicant_id: int | None
    contractor_id: int | None
    cadastral_number: str
    address_raw: str
    lat: float | None
    lng: float | None
    plot_id: int | None
    project_description: str
    project_type: str
    building_purpose: str
    source_url: str


class ErrorOut(Schema):
    detail: str


def _parse_bbox(bbox_str: str) -> tuple[float, float, float, float]:
    """Parse 'min_lng,min_lat,max_lng,max_lat' into a tuple of floats."""
    parts = bbox_str.split(",")
    if len(parts) != 4:
        msg = f"bbox must have exactly 4 values, got {len(parts)}"
        raise ValueError(msg)
    return tuple(float(p.strip()) for p in parts)  # type: ignore[return-value]


@router.get("/", response={200: list[PermitOut], 400: ErrorOut})
def list_permits(
    request,
    bbox: str,
    issued_after: str | None = None,
    status: str | None = None,
):
    """List building permits within a bounding box.

    Query parameters:
        bbox: min_lng,min_lat,max_lng,max_lat
        issued_after: YYYY-MM-DD (optional)
        status: e.g. 'issued', 'in_progress' (optional)
    """
    try:
        min_lng, min_lat, max_lng, max_lat = _parse_bbox(bbox)
    except ValueError as e:
        return 400, {"detail": str(e)}

    bbox_polygon = Polygon.from_bbox((min_lng, min_lat, max_lng, max_lat))
    bbox_polygon.srid = 4326

    qs = BuildingPermit.objects.filter(location__within=bbox_polygon)

    if issued_after:
        qs = qs.filter(issued_at__gte=issued_after)
    if status:
        qs = qs.filter(status=status)

    qs = qs.order_by("-issued_at")[:MAX_RESULTS]

    return 200, [_permit_to_out(p) for p in qs]


def _permit_to_out(permit: BuildingPermit) -> dict:
    return {
        "id": permit.id,
        "permit_number": permit.permit_number,
        "permit_type": permit.permit_type,
        "status": permit.status,
        "issued_at": permit.issued_at.isoformat() if permit.issued_at else None,
        "applicant_name": permit.applicant_name,
        "applicant_id": permit.applicant_id,
        "contractor_id": permit.contractor_id,
        "cadastral_number": permit.cadastral_number,
        "address_raw": permit.address_raw,
        "lat": permit.location.y if permit.location else None,
        "lng": permit.location.x if permit.location else None,
        "plot_id": permit.plot_id,
        "project_description": permit.project_description,
        "project_type": permit.project_type,
        "building_purpose": permit.building_purpose,
        "source_url": permit.source_url,
    }
