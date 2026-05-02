from __future__ import annotations

import logging

from celery import shared_task
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def notify_saved_searches() -> dict:
    """Check each active saved search for new listings and send digest emails."""
    from apps.listings.models import Listing
    from apps.search.models import SavedSearch

    total_searches = 0
    total_notified = 0
    total_listings = 0

    for search in SavedSearch.objects.filter(is_active=True):
        total_searches += 1
        center = Point(search.lng, search.lat, srid=4326)

        qs = Listing.objects.filter(
            is_active=True,
            location__dwithin=(center, D(m=search.radius_m)),
            first_seen_at__gt=search.last_notified_at,
        )

        if search.min_price is not None:
            qs = qs.filter(price__gte=search.min_price)
        if search.max_price is not None:
            qs = qs.filter(price__lte=search.max_price)
        if search.rooms is not None:
            qs = qs.filter(rooms=search.rooms)
        if search.property_type:
            qs = qs.filter(property_type=search.property_type)
        if search.listing_type:
            qs = qs.filter(listing_type=search.listing_type)
        if search.is_new_construction is not None:
            qs = qs.filter(is_new_construction=search.is_new_construction)

        new_listings = list(qs.order_by("-first_seen_at")[:50])
        if not new_listings:
            continue

        total_notified += 1
        total_listings += len(new_listings)

        _send_digest_email(search, new_listings)

        search.last_notified_at = timezone.now()
        search.save(update_fields=["last_notified_at"])

    logger.info(
        "Saved search notifications: %d searches checked, %d notified, %d listings total",
        total_searches,
        total_notified,
        total_listings,
    )
    return {
        "searches_checked": total_searches,
        "notified": total_notified,
        "listings_sent": total_listings,
    }


def _send_digest_email(search, listings: list) -> None:
    """Send a digest email for a saved search with new matching listings."""
    lines = [f'Nauji skelbimai pagal paiešką "{search.name}":\n']
    for listing in listings:
        price_str = f"{listing.price} EUR" if listing.price else "Kaina nenurodyta"
        lines.append(f"  - {listing.title} | {price_str} | {listing.source_url}")
    lines.append(f"\nIš viso: {len(listings)} nauji skelbimai.")

    body = "\n".join(lines)

    send_mail(
        subject=f"[NT] {len(listings)} nauji skelbimai: {search.name}",
        message=body,
        from_email="noreply@nt.local",
        recipient_list=["napalias@gmail.com"],
    )
