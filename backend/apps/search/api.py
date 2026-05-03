from __future__ import annotations

import logging
from decimal import Decimal
from functools import wraps

import requests
from django.conf import settings
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django_ratelimit.core import is_ratelimited
from ninja import Query, Router, Schema

from apps.cadastre.models import CadastralPlot, HeritageObject, SpecialLandUseCondition
from apps.developers.models import Developer
from apps.listings.models import ExcludedListing, Listing
from apps.permits.models import BuildingPermit
from apps.planning.models import PlanningDocument
from apps.search.models import SavedSearch

logger = logging.getLogger(__name__)

router = Router(tags=["search"])


def ratelimit(rate: str = "60/m", key: str = "ip", group: str | None = None):
    """Decorator to apply rate limiting to django-ninja endpoints."""

    def decorator(func):
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            ratelimited = is_ratelimited(
                request=request,
                group=group or f"api:{func.__name__}",
                key=key,
                rate=rate,
                increment=True,
            )
            if ratelimited:
                return JsonResponse(
                    {"detail": "Rate limit exceeded. Try again later."},
                    status=429,
                )
            return func(request, *args, **kwargs)

        return wrapper

    return decorator


MAX_RESULTS = 200
MAX_RADIUS_M = 50_000


class SearchParams(Schema):
    lat: float
    lng: float
    radius_m: int = settings.DEFAULT_SEARCH_RADIUS_M
    min_price: float | None = None
    max_price: float | None = None
    rooms: int | None = None
    property_type: str | None = None
    listing_type: str | None = None
    is_new_construction: bool | None = None


class ListingOut(Schema):
    id: int
    source: str
    source_url: str
    title: str
    property_type: str
    listing_type: str
    price: float | None
    price_per_sqm: float | None
    currency: str
    area_sqm: float | None
    plot_area_ares: float | None
    rooms: int | None
    floor: int | None
    total_floors: int | None
    year_built: int | None
    building_type: str
    is_new_construction: bool
    address_raw: str
    city: str
    district: str
    lat: float
    lng: float
    photo_urls: list[str]
    distance_m: float


class SearchResponse(Schema):
    center: dict
    radius_m: int
    count: int
    results: list[ListingOut]


class GeocodeResult(Schema):
    lat: float
    lng: float
    display_name: str


@router.get("/search", response=SearchResponse)
@ratelimit(rate="60/m", key="ip")
def search_listings(request, params: Query[SearchParams]):
    """Search listings within a radius of a point, with optional filters."""
    center = Point(params.lng, params.lat, srid=4326)
    radius_m = min(params.radius_m, MAX_RADIUS_M)

    qs = (
        Listing.objects.filter(
            is_active=True,
            location__dwithin=(center, D(m=radius_m)),
        )
        .exclude(exclusion__isnull=False)
        .annotate(distance=Distance("location", center))
        .order_by("distance")
    )

    if params.min_price is not None:
        qs = qs.filter(price__gte=params.min_price)
    if params.max_price is not None:
        qs = qs.filter(price__lte=params.max_price)
    if params.rooms is not None:
        qs = qs.filter(rooms=params.rooms)
    if params.property_type:
        qs = qs.filter(property_type=params.property_type)
    if params.listing_type:
        qs = qs.filter(listing_type=params.listing_type)
    if params.is_new_construction is not None:
        qs = qs.filter(is_new_construction=params.is_new_construction)

    listings = qs[:MAX_RESULTS]

    return {
        "center": {"lat": params.lat, "lng": params.lng},
        "radius_m": radius_m,
        "count": len(listings),
        "results": [_listing_to_out(listing) for listing in listings],
    }


@router.get("/geocode", response=list[GeocodeResult])
@ratelimit(rate="60/m", key="ip")
def geocode(request, q: str):
    """Geocode an address via Nominatim. Returns top 5 matches."""
    try:
        resp = requests.get(
            f"{settings.NOMINATIM_URL}/search",
            params={
                "q": q,
                "format": "json",
                "limit": 5,
                "countrycodes": "lt",
                "addressdetails": 1,
            },
            timeout=5,
        )
        resp.raise_for_status()
    except requests.RequestException:
        logger.exception("Nominatim request failed for q=%s", q)
        return []

    return [
        {
            "lat": float(r["lat"]),
            "lng": float(r["lon"]),
            "display_name": r["display_name"],
        }
        for r in resp.json()
    ]


# --- Saved searches ---


class SavedSearchIn(Schema):
    name: str
    lat: float
    lng: float
    radius_m: int = settings.DEFAULT_SEARCH_RADIUS_M
    min_price: float | None = None
    max_price: float | None = None
    rooms: int | None = None
    property_type: str = ""
    listing_type: str = "sale"
    is_new_construction: bool | None = None


class SavedSearchOut(Schema):
    id: int
    name: str
    lat: float
    lng: float
    radius_m: int
    min_price: float | None
    max_price: float | None
    rooms: int | None
    property_type: str
    listing_type: str
    is_new_construction: bool | None
    is_active: bool
    created_at: str
    last_notified_at: str


@router.post("/searches", response=SavedSearchOut)
def create_saved_search(request, payload: SavedSearchIn):
    """Save the current search filters for notifications."""
    search = SavedSearch.objects.create(
        name=payload.name,
        lat=payload.lat,
        lng=payload.lng,
        radius_m=payload.radius_m,
        min_price=Decimal(str(payload.min_price)) if payload.min_price is not None else None,
        max_price=Decimal(str(payload.max_price)) if payload.max_price is not None else None,
        rooms=payload.rooms,
        property_type=payload.property_type,
        listing_type=payload.listing_type,
        is_new_construction=payload.is_new_construction,
    )
    return _saved_search_to_out(search)


@router.get("/searches", response=list[SavedSearchOut])
def list_saved_searches(request):
    """List all saved searches."""
    return [_saved_search_to_out(s) for s in SavedSearch.objects.all()]


@router.delete("/searches/{search_id}")
def delete_saved_search(request, search_id: int):
    """Delete a saved search."""
    search = get_object_or_404(SavedSearch, id=search_id)
    search.delete()
    return {"ok": True}


def _saved_search_to_out(search: SavedSearch) -> dict:
    return {
        "id": search.id,
        "name": search.name,
        "lat": search.lat,
        "lng": search.lng,
        "radius_m": search.radius_m,
        "min_price": float(search.min_price) if search.min_price is not None else None,
        "max_price": float(search.max_price) if search.max_price is not None else None,
        "rooms": search.rooms,
        "property_type": search.property_type,
        "listing_type": search.listing_type,
        "is_new_construction": search.is_new_construction,
        "is_active": search.is_active,
        "created_at": search.created_at.isoformat(),
        "last_notified_at": search.last_notified_at.isoformat(),
    }


def _listing_to_out(listing: Listing) -> dict:
    return {
        "id": listing.id,
        "source": listing.source,
        "source_url": listing.source_url,
        "title": listing.title,
        "property_type": listing.property_type,
        "listing_type": listing.listing_type,
        "price": float(listing.price) if listing.price else None,
        "price_per_sqm": float(listing.price_per_sqm) if listing.price_per_sqm else None,
        "currency": listing.currency,
        "area_sqm": float(listing.area_sqm) if listing.area_sqm else None,
        "plot_area_ares": float(listing.plot_area_ares) if listing.plot_area_ares else None,
        "rooms": listing.rooms,
        "floor": listing.floor,
        "total_floors": listing.total_floors,
        "year_built": listing.year_built,
        "building_type": listing.building_type,
        "is_new_construction": listing.is_new_construction,
        "address_raw": listing.address_raw,
        "city": listing.city,
        "district": listing.district,
        "lat": listing.location.y,
        "lng": listing.location.x,
        "photo_urls": listing.photo_urls or [],
        "distance_m": round(listing.distance.m, 1),
    }


def _listing_to_out_simple(listing: Listing) -> dict:
    """Listing serializer for contexts where distance annotation is not available."""
    return {
        "id": listing.id,
        "source": listing.source,
        "source_url": listing.source_url,
        "title": listing.title,
        "property_type": listing.property_type,
        "listing_type": listing.listing_type,
        "price": float(listing.price) if listing.price else None,
        "price_per_sqm": float(listing.price_per_sqm) if listing.price_per_sqm else None,
        "currency": listing.currency,
        "area_sqm": float(listing.area_sqm) if listing.area_sqm else None,
        "plot_area_ares": float(listing.plot_area_ares) if listing.plot_area_ares else None,
        "rooms": listing.rooms,
        "floor": listing.floor,
        "total_floors": listing.total_floors,
        "year_built": listing.year_built,
        "building_type": listing.building_type,
        "is_new_construction": listing.is_new_construction,
        "address_raw": listing.address_raw,
        "city": listing.city,
        "district": listing.district,
        "lat": listing.location.y,
        "lng": listing.location.x,
        "photo_urls": listing.photo_urls or [],
    }


# --- Multi-layer full search ---


MAX_FULL_RESULTS_PER_LAYER = 200

PROPERTY_REPORT_NEARBY_M = 100


class FullSearchParams(Schema):
    lat: float
    lng: float
    radius_m: int = settings.DEFAULT_SEARCH_RADIUS_M


class PermitSummaryOut(Schema):
    id: int
    permit_number: str
    applicant_name: str
    status: str
    issued_at: str | None
    building_purpose: str
    project_type: str
    lat: float | None
    lng: float | None
    source_url: str


class DeveloperSummaryOut(Schema):
    id: int
    name: str
    company_code: str
    active_permits_count: int
    registered_point: list[float] | None


class PlanningSummaryOut(Schema):
    id: int
    title: str
    doc_type: str
    status: str
    max_floors: int | None
    allowed_uses: list
    source_url: str


class HeritageOut(Schema):
    type: str = "heritage_zone"
    kvr_code: str
    name: str
    protection_level: str


class SpecialLandUseOut(Schema):
    type: str = "special_land_use"
    category: str
    description: str


class RestrictionsOut(Schema):
    heritage: list[HeritageOut]
    special_land_use: list[SpecialLandUseOut]


class FullSearchResponse(Schema):
    center: dict
    radius_m: int
    listings: list[dict]
    permits: list[PermitSummaryOut]
    developers: list[DeveloperSummaryOut]
    planning: list[PlanningSummaryOut]
    restrictions: RestrictionsOut


@router.get("/search/full", response=FullSearchResponse)
@ratelimit(rate="60/m", key="ip")
def search_full(request, params: Query[FullSearchParams]):
    """Multi-layer search: returns listings, permits, developers, planning docs,
    and restrictions within a radius of a point."""
    center = Point(params.lng, params.lat, srid=4326)
    radius_m = min(params.radius_m, MAX_RADIUS_M)

    # Listings
    listings_qs = (
        Listing.objects.filter(
            is_active=True,
            location__dwithin=(center, D(m=radius_m)),
        )
        .exclude(exclusion__isnull=False)
        .annotate(distance=Distance("location", center))
        .order_by("distance")[:MAX_FULL_RESULTS_PER_LAYER]
    )

    # Permits
    permits_qs = BuildingPermit.objects.filter(
        location__dwithin=(center, D(m=radius_m)),
    ).order_by("-issued_at")[:MAX_FULL_RESULTS_PER_LAYER]

    # Developers (by registered office)
    developers_qs = Developer.objects.filter(
        registered_address_point__dwithin=(center, D(m=radius_m)),
    ).order_by("name")[:MAX_FULL_RESULTS_PER_LAYER]

    # Planning documents (boundary intersects search circle)
    planning_qs = PlanningDocument.objects.filter(
        boundary__dwithin=(center, D(m=radius_m)),
    ).order_by("-approved_at")[:MAX_FULL_RESULTS_PER_LAYER]

    # Restrictions - heritage
    heritage_qs = HeritageObject.objects.filter(
        geometry__dwithin=(center, D(m=radius_m)),
    )[:MAX_FULL_RESULTS_PER_LAYER]

    # Restrictions - special land use
    slu_qs = SpecialLandUseCondition.objects.filter(
        geometry__dwithin=(center, D(m=radius_m)),
    )[:MAX_FULL_RESULTS_PER_LAYER]

    return {
        "center": {"lat": params.lat, "lng": params.lng},
        "radius_m": radius_m,
        "listings": [_listing_to_out(listing) for listing in listings_qs],
        "permits": [_permit_summary(p) for p in permits_qs],
        "developers": [_developer_summary(d) for d in developers_qs],
        "planning": [_planning_summary(doc) for doc in planning_qs],
        "restrictions": {
            "heritage": [_heritage_out(h) for h in heritage_qs],
            "special_land_use": [_slu_out(s) for s in slu_qs],
        },
    }


# --- Property report ---


class CadastralPlotOut(Schema):
    id: int
    cadastral_number: str
    area_sqm: float
    purpose: str
    purpose_category: str
    municipality: str


class PropertyReportResponse(Schema):
    plot: CadastralPlotOut | None
    listings: list[dict]
    permits: list[PermitSummaryOut]
    planning: list[PlanningSummaryOut]
    developers: list[DeveloperSummaryOut]
    restrictions: RestrictionsOut


class PropertyReportErrorOut(Schema):
    detail: str


@router.get(
    "/property/{path:cadastral_number}",
    response=PropertyReportResponse,
)
@ratelimit(rate="60/m", key="ip")
def property_report(request, cadastral_number: str):
    """Assemble everything we know about a single cadastral plot."""
    plot = get_object_or_404(CadastralPlot, cadastral_number=cadastral_number)

    plot_geom = plot.geometry

    # Listings within 100m of the plot centroid
    centroid = plot_geom.centroid
    nearby_listings = (
        Listing.objects.filter(
            is_active=True,
            location__dwithin=(centroid, D(m=PROPERTY_REPORT_NEARBY_M)),
        )
        .exclude(exclusion__isnull=False)
        .order_by("-scraped_at")[:MAX_FULL_RESULTS_PER_LAYER]
    )

    # Permits matching the cadastral number
    permits = BuildingPermit.objects.filter(
        cadastral_number=cadastral_number,
    ).order_by("-issued_at")[:MAX_FULL_RESULTS_PER_LAYER]

    # Planning documents whose boundary intersects the plot geometry
    planning = PlanningDocument.objects.filter(
        boundary__intersects=plot_geom,
    ).order_by("-approved_at")[:MAX_FULL_RESULTS_PER_LAYER]

    # Heritage objects intersecting the plot
    heritage = HeritageObject.objects.filter(
        geometry__intersects=plot_geom,
    )[:MAX_FULL_RESULTS_PER_LAYER]

    # Special land use conditions intersecting the plot
    slu = SpecialLandUseCondition.objects.filter(
        geometry__intersects=plot_geom,
    )[:MAX_FULL_RESULTS_PER_LAYER]

    # Developers with permits on this plot
    developer_ids = (
        BuildingPermit.objects.filter(cadastral_number=cadastral_number)
        .exclude(applicant__isnull=True)
        .values_list("applicant_id", flat=True)
        .distinct()
    )
    developers = Developer.objects.filter(id__in=developer_ids)

    return 200, {
        "plot": _cadastral_plot_out(plot),
        "listings": [_listing_to_out_simple(listing) for listing in nearby_listings],
        "permits": [_permit_summary(p) for p in permits],
        "planning": [_planning_summary(doc) for doc in planning],
        "developers": [_developer_summary(d) for d in developers],
        "restrictions": {
            "heritage": [_heritage_out(h) for h in heritage],
            "special_land_use": [_slu_out(s) for s in slu],
        },
    }


# --- Serialization helpers ---


def _permit_summary(permit: BuildingPermit) -> dict:
    return {
        "id": permit.id,
        "permit_number": permit.permit_number,
        "applicant_name": permit.applicant_name,
        "status": permit.status,
        "issued_at": permit.issued_at.isoformat() if permit.issued_at else None,
        "building_purpose": permit.building_purpose,
        "project_type": permit.project_type,
        "lat": permit.location.y if permit.location else None,
        "lng": permit.location.x if permit.location else None,
        "source_url": permit.source_url,
    }


def _developer_summary(dev: Developer) -> dict:
    active_count = BuildingPermit.objects.filter(
        applicant=dev, status__in=["issued", "in_progress"]
    ).count()
    return {
        "id": dev.id,
        "name": dev.name,
        "company_code": dev.company_code,
        "active_permits_count": active_count,
        "registered_point": (
            [dev.registered_address_point.y, dev.registered_address_point.x]
            if dev.registered_address_point
            else None
        ),
    }


def _planning_summary(doc: PlanningDocument) -> dict:
    return {
        "id": doc.id,
        "title": doc.title,
        "doc_type": doc.doc_type,
        "status": doc.status,
        "max_floors": doc.max_floors,
        "allowed_uses": doc.allowed_uses or [],
        "source_url": doc.source_url,
    }


def _heritage_out(obj: HeritageObject) -> dict:
    return {
        "type": "heritage_zone",
        "kvr_code": obj.kvr_code,
        "name": obj.name,
        "protection_level": obj.protection_level,
    }


def _slu_out(cond: SpecialLandUseCondition) -> dict:
    return {
        "type": "special_land_use",
        "category": cond.category,
        "description": cond.description,
    }


def _cadastral_plot_out(plot: CadastralPlot) -> dict:
    return {
        "id": plot.id,
        "cadastral_number": plot.cadastral_number,
        "area_sqm": plot.area_sqm,
        "purpose": plot.purpose,
        "purpose_category": plot.purpose_category,
        "municipality": plot.municipality,
    }


# --- Listing exclusion ---


class ValidityCheckResponse(Schema):
    checked: int
    deactivated: int


@router.post("/check-validity", response=ValidityCheckResponse)
def check_validity(request):
    """Trigger a synchronous validity check on the oldest-seen active listings."""
    from apps.listings.tasks import check_listing_validity

    result = check_listing_validity(batch_size=50)
    return result


class ExcludeIn(Schema):
    reason: str = ""


class ExcludeOut(Schema):
    listing_id: int
    reason: str
    excluded_at: str


@router.post("/exclude/{listing_id}", response=ExcludeOut)
def exclude_listing(request, listing_id: int, payload: ExcludeIn):
    """Mark a listing as excluded so it no longer appears in search results."""
    listing = get_object_or_404(Listing, id=listing_id)
    exclusion, _created = ExcludedListing.objects.update_or_create(
        listing=listing,
        defaults={"reason": payload.reason},
    )
    return {
        "listing_id": listing.id,
        "reason": exclusion.reason,
        "excluded_at": exclusion.excluded_at.isoformat(),
    }


@router.delete("/exclude/{listing_id}")
def unexclude_listing(request, listing_id: int):
    """Remove the exclusion from a listing so it appears in search results again."""
    listing = get_object_or_404(Listing, id=listing_id)
    deleted, _ = ExcludedListing.objects.filter(listing=listing).delete()
    if not deleted:
        return {"ok": False, "detail": "Listing was not excluded"}
    return {"ok": True}
