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

from apps.listings.models import Listing
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
