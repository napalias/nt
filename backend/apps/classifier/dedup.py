"""AI-powered cross-portal duplicate detection.

Strategy:
1. Find candidate pairs using cheap spatial + attribute filters
   (within 50m, similar area ±5%, same room count)
2. Ask Claude to compare the pair and decide if they're the same property
3. Group confirmed matches into ListingCluster records
"""

from __future__ import annotations

import logging

import anthropic
from django.conf import settings
from django.contrib.gis.measure import D

from apps.classifier.models import ListingCluster
from apps.listings.models import Listing

logger = logging.getLogger(__name__)

DEDUP_MODEL = "claude-haiku-4-5-20251001"

COMPARE_TOOL = {
    "name": "compare_listings",
    "description": "Decide if two real estate listings are the same property",
    "input_schema": {
        "type": "object",
        "properties": {
            "is_same": {
                "type": "boolean",
                "description": "True if both listings describe the same physical property",
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "How confident you are (0.0-1.0)",
            },
            "reasoning": {
                "type": "string",
                "description": "Brief explanation of why they match or don't",
            },
        },
        "required": ["is_same", "confidence", "reasoning"],
    },
}

COMPARE_PROMPT = """\
You are comparing two real estate listings from different portals to determine \
if they describe the SAME physical property.

Two listings are the same property if they refer to the same house/flat/plot at \
the same address, even if descriptions differ slightly between portals.

Key signals of a match:
- Same or very similar address
- Same area (±5%) and room count
- Same or very close price
- Similar description details (heating, year, building type)
- Photos from the same property (even if different angles)

Key signals they're different:
- Different addresses or locations
- Significantly different area or room count
- Different property type (house vs flat)
- Clearly different properties from description

## Listing A ({source_a})
{listing_a}

## Listing B ({source_b})
{listing_b}
"""


def _listing_summary(listing: Listing) -> str:
    parts = [
        f"Title: {listing.title}",
        f"Price: {listing.price} {listing.currency}" if listing.price else "Price: N/A",
        f"Area: {listing.area_sqm} kv.m" if listing.area_sqm else "",
        f"Plot: {listing.plot_area_ares} a" if listing.plot_area_ares else "",
        f"Rooms: {listing.rooms}" if listing.rooms else "",
        f"Address: {listing.address_raw}",
        f"City: {listing.city}",
        f"Year: {listing.year_built}" if listing.year_built else "",
        f"Building: {listing.get_building_type_display()}" if listing.building_type else "",
        f"Heating: {listing.heating_type}" if listing.heating_type else "",
        f"New: {'Yes' if listing.is_new_construction else 'No'}",
    ]
    if listing.description:
        parts.append(f"Description: {listing.description[:500]}")
    return "\n".join(p for p in parts if p)


def find_candidates(radius_m: int = 50) -> list[tuple[Listing, Listing]]:
    """Find listing pairs from different sources that might be the same property."""
    active = Listing.objects.filter(is_active=True).order_by("id")
    candidates = []
    seen = set()

    for listing in active.iterator():
        nearby = (
            Listing.objects.filter(is_active=True)
            .exclude(source=listing.source)
            .exclude(pk=listing.pk)
            .filter(location__dwithin=(listing.location, D(m=radius_m)))
        )

        if listing.rooms:
            nearby = nearby.filter(rooms=listing.rooms)

        for other in nearby:
            pair_key = tuple(sorted([listing.pk, other.pk]))
            if pair_key in seen:
                continue
            seen.add(pair_key)

            if listing.area_sqm and other.area_sqm:
                ratio = float(listing.area_sqm) / float(other.area_sqm)
                if ratio < 0.9 or ratio > 1.1:
                    continue

            already_clustered = (
                ListingCluster.objects.filter(listings=listing).filter(listings=other).exists()
            )
            if already_clustered:
                continue

            candidates.append((listing, other))

    logger.info("Found %d candidate pairs for dedup", len(candidates))
    return candidates


def compare_pair(listing_a: Listing, listing_b: Listing) -> dict:
    """Ask Claude whether two listings are the same property."""
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is not set")

    client = anthropic.Anthropic(api_key=api_key)

    prompt = COMPARE_PROMPT.format(
        source_a=listing_a.source,
        listing_a=_listing_summary(listing_a),
        source_b=listing_b.source,
        listing_b=_listing_summary(listing_b),
    )

    response = client.messages.create(
        model=DEDUP_MODEL,
        max_tokens=256,
        tools=[COMPARE_TOOL],
        tool_choice={"type": "tool", "name": "compare_listings"},
        messages=[{"role": "user", "content": prompt}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "compare_listings":
            return block.input

    raise ValueError("No compare_listings tool call in response")


def run_dedup(radius_m: int = 50, min_confidence: float = 0.7) -> list[ListingCluster]:
    """Full dedup run: find candidates, compare via AI, create clusters."""
    candidates = find_candidates(radius_m=radius_m)
    created_clusters = []

    for listing_a, listing_b in candidates:
        try:
            result = compare_pair(listing_a, listing_b)
        except Exception:
            logger.exception(
                "Failed to compare listings %s and %s",
                listing_a.pk,
                listing_b.pk,
            )
            continue

        if not result["is_same"] or result["confidence"] < min_confidence:
            logger.debug(
                "Not a match: %s vs %s (confidence=%.2f)",
                listing_a.pk,
                listing_b.pk,
                result["confidence"],
            )
            continue

        existing_a = ListingCluster.objects.filter(listings=listing_a).first()
        existing_b = ListingCluster.objects.filter(listings=listing_b).first()

        if existing_a and existing_b and existing_a == existing_b:
            continue

        if existing_a:
            existing_a.listings.add(listing_b)
            existing_a.confidence = max(existing_a.confidence, result["confidence"])
            existing_a.save(update_fields=["confidence", "updated_at"])
            cluster = existing_a
        elif existing_b:
            existing_b.listings.add(listing_a)
            existing_b.confidence = max(existing_b.confidence, result["confidence"])
            existing_b.save(update_fields=["confidence", "updated_at"])
            cluster = existing_b
        else:
            canonical = _pick_canonical(listing_a, listing_b)
            cluster = ListingCluster.objects.create(
                confidence=result["confidence"],
                reasoning=result["reasoning"],
                canonical=canonical,
            )
            cluster.listings.add(listing_a, listing_b)
            created_clusters.append(cluster)

        logger.info(
            "Matched: %s (%s) ↔ %s (%s) — %.0f%% — %s",
            listing_a.pk,
            listing_a.source,
            listing_b.pk,
            listing_b.source,
            result["confidence"] * 100,
            result["reasoning"][:80],
        )

    logger.info("Dedup complete: %d new clusters created", len(created_clusters))
    return created_clusters


def _pick_canonical(a: Listing, b: Listing) -> Listing:
    """Pick the best listing as canonical (most info, best source)."""
    source_priority = {"aruodas": 3, "domoplius": 2, "skelbiu": 1}
    score_a = source_priority.get(a.source, 0)
    score_b = source_priority.get(b.source, 0)

    if a.description and not b.description:
        score_a += 1
    elif b.description and not a.description:
        score_b += 1

    if len(a.photo_urls or []) > len(b.photo_urls or []):
        score_a += 1
    elif len(b.photo_urls or []) > len(a.photo_urls or []):
        score_b += 1

    return a if score_a >= score_b else b
