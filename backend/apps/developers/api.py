from __future__ import annotations

from django.contrib.gis.geos import Polygon
from django.shortcuts import get_object_or_404
from ninja import Query, Router, Schema

from apps.developers.models import Developer

router = Router(tags=["developers"])


class BboxParams(Schema):
    bbox: str  # "min_lng,min_lat,max_lng,max_lat"


class DeveloperOut(Schema):
    id: int
    company_code: str
    name: str
    nace_codes: list[str]
    registered_address: str
    lat: float | None
    lng: float | None
    founded: str | None
    status: str
    employee_count: int | None


MAX_RESULTS = 200


@router.get("/", response=list[DeveloperOut])
def list_developers(request, params: Query[BboxParams]):
    """List developers with registered offices within a bounding box."""
    parts = params.bbox.split(",")
    if len(parts) != 4:
        return []

    try:
        min_lng, min_lat, max_lng, max_lat = (float(p) for p in parts)
    except ValueError:
        return []

    bbox_polygon = Polygon.from_bbox((min_lng, min_lat, max_lng, max_lat))
    bbox_polygon.srid = 4326

    qs = Developer.objects.filter(
        registered_address_point__within=bbox_polygon,
    ).order_by("name")[:MAX_RESULTS]

    return [_developer_to_out(d) for d in qs]


@router.get("/{developer_id}", response=DeveloperOut)
def get_developer(request, developer_id: int):
    """Get a single developer by ID."""
    dev = get_object_or_404(Developer, id=developer_id)
    return _developer_to_out(dev)


def _developer_to_out(dev: Developer) -> dict:
    return {
        "id": dev.id,
        "company_code": dev.company_code,
        "name": dev.name,
        "nace_codes": dev.nace_codes or [],
        "registered_address": dev.registered_address,
        "lat": dev.registered_address_point.y if dev.registered_address_point else None,
        "lng": dev.registered_address_point.x if dev.registered_address_point else None,
        "founded": dev.founded.isoformat() if dev.founded else None,
        "status": dev.status,
        "employee_count": dev.employee_count,
    }
