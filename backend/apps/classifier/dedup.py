"""AI-powered cross-portal duplicate detection.

Strategy:
1. Find candidate pairs using cheap spatial + attribute filters
   - exact/street/unknown coordinates: within 50 m, similar area, same room count
   - ``location_precision="city"`` (coordinates are the city centre, so every same-city
     listing is "within 50 m"): same city, price within 5% and area within 5%
   - pairs already judged are skipped (pair cache, below)
2. Judge each pair, at most ``DEDUP_MAX_PAIRS_PER_RUN`` per run: Jev answers the "same
   property?" yes/no question; its P(same) between ``DEDUP_GREY_BAND_LOW`` and
   ``DEDUP_GREY_BAND_HIGH`` is the grey band where Claude (``CLAUDE_FAST_MODEL``) decides.
   JEV_MODE=on: Jev decides outside the band. shadow: Claude decides every pair and Jev is
   only logged. off: Claude decides every pair.
3. Group confirmed matches into ListingCluster records. When the two listings already sit in
   different clusters, the clusters merge into the older one.

Pair cache: every judged pair leaves a ``JudgmentLog`` row (kind="dedup", question
"same_property") whose subject_id is a digest of the two listings' content hashes, so the
pair is judged again only after one of the listings changes. judge.ask() logs in shadow and
on mode only; in off mode this module writes the same row itself, because off is the default
and is where the nightly re-payment happened.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.gis.measure import D
from django.db import transaction
from django.db.models import Q

from apps.classifier import judge, llm
from apps.classifier.llm import get_client
from apps.classifier.models import JudgmentLog, ListingCluster
from apps.listings.models import Listing

logger = logging.getLogger(__name__)

JUDGMENT_KIND = "dedup"
QUESTION_NAME = "same_property"
# A city-centre match needs price and area this close (fraction of the larger value).
CITY_TOLERANCE = Decimal("0.05")

SAME_PROPERTY = judge.Question.yes_no(
    QUESTION_NAME,
    "Do listing `a` and listing `b`, from two different portals, describe the same physical "
    "property: the same house or plot at the same address, even if the wording, photos or "
    "price differ a little?",
    yes="Same property listed twice",
    no="Two different properties",
)

# Covers adaptive thinking as well as the short answer.
COMPARE_MAX_TOKENS = 1024

COMPARE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_same": {
            "type": "boolean",
            "description": "True if both listings describe the same physical property",
        },
        "confidence": {
            "type": "number",
            "description": "How confident you are, from 0.0 to 1.0",
        },
        "reasoning": {
            "type": "string",
            "description": "Brief explanation of why they match or don't",
        },
    },
    "required": ["is_same", "confidence", "reasoning"],
    "additionalProperties": False,
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


def pair_key(a: Listing, b: Listing) -> str:
    """Order-independent key of a listing pair, built from the two content hashes.

    A digest, so it fits ``JudgmentLog.subject_id`` (64 characters). It changes when either
    listing's content changes, which is what makes the pair worth judging again.
    """
    first, second = sorted([a.content_hash, b.content_hash])
    return hashlib.sha256(f"{first}\n{second}".encode()).hexdigest()


def _judged_pair_keys() -> set[str]:
    """Pair keys that already have a logged judgment where somebody decided."""
    return set(
        JudgmentLog.objects.filter(
            kind=JUDGMENT_KIND,
            question=QUESTION_NAME,
            decided_by__in=[JudgmentLog.DecidedBy.JEV, JudgmentLog.DecidedBy.FALLBACK],
        )
        .values_list("subject_id", flat=True)
        .distinct()
    )


def _within_tolerance(x: Decimal, y: Decimal) -> bool:
    return abs(x - y) <= CITY_TOLERANCE * max(x, y)


def _active_others(listing: Listing):
    return (
        Listing.objects.filter(is_active=True).exclude(source=listing.source).exclude(pk=listing.pk)
    )


def _city_matches(listing: Listing) -> list[Listing]:
    """Other-source listings in the same city with price and area within 5%.

    Used when ``listing``'s coordinates are only the city centre. Price and area are required
    on both sides: without them nothing separates one house from the next in a town.
    """
    if not (listing.city and listing.price and listing.area_sqm):
        return []
    floor = Decimal(1) - CITY_TOLERANCE
    nearby = _active_others(listing).filter(
        city__iexact=listing.city,
        price__gte=listing.price * floor,
        price__lte=listing.price / floor,
        area_sqm__gte=listing.area_sqm * floor,
        area_sqm__lte=listing.area_sqm / floor,
    )
    if listing.rooms:
        nearby = nearby.filter(rooms=listing.rooms)
    return [
        other
        for other in nearby
        if _within_tolerance(listing.price, other.price)
        and _within_tolerance(listing.area_sqm, other.area_sqm)
    ]


def _near_matches(listing: Listing, radius_m: int) -> list[Listing]:
    """Other-source listings within ``radius_m`` with a similar area and the same rooms.

    City-centre listings are left out: their coordinates say nothing about the property, and
    they are matched from their own side by ``_city_matches``.
    """
    nearby = (
        _active_others(listing)
        .exclude(location_precision=Listing.LocationPrecision.CITY)
        .filter(location__dwithin=(listing.location, D(m=radius_m)))
    )
    if listing.rooms:
        nearby = nearby.filter(rooms=listing.rooms)

    matches = []
    for other in nearby:
        if listing.area_sqm and other.area_sqm:
            ratio = float(listing.area_sqm) / float(other.area_sqm)
            if ratio < 0.9 or ratio > 1.1:
                continue
        matches.append(other)
    return matches


def find_candidates(radius_m: int = 50) -> list[tuple[Listing, Listing]]:
    """Find listing pairs from different sources that might be the same property.

    Pairs already in one cluster, and pairs with a logged judgment for the listings' current
    content, are left out.
    """
    active = Listing.objects.filter(is_active=True).order_by("id")
    candidates = []
    seen = set()
    judged = _judged_pair_keys()
    skipped_judged = 0

    for listing in active.iterator():
        if listing.location_precision == Listing.LocationPrecision.CITY:
            nearby = _city_matches(listing)
        else:
            nearby = _near_matches(listing, radius_m)

        for other in nearby:
            ids = tuple(sorted([listing.pk, other.pk]))
            if ids in seen:
                continue
            seen.add(ids)

            if pair_key(listing, other) in judged:
                skipped_judged += 1
                continue

            already_clustered = (
                ListingCluster.objects.filter(listings=listing).filter(listings=other).exists()
            )
            if already_clustered:
                continue

            candidates.append((listing, other))

    logger.info(
        "Found %d candidate pairs for dedup (%d skipped, already judged)",
        len(candidates),
        skipped_judged,
    )
    return candidates


def compare_pair(listing_a: Listing, listing_b: Listing) -> dict:
    """Ask Claude whether two listings are the same property."""
    return _compare_pair(get_client(), listing_a, listing_b)


def _compare_pair(client, listing_a: Listing, listing_b: Listing) -> dict:
    prompt = COMPARE_PROMPT.format(
        source_a=listing_a.source,
        listing_a=_listing_summary(listing_a),
        source_b=listing_b.source,
        listing_b=_listing_summary(listing_b),
    )

    data = llm.call_structured(
        client,
        model=settings.CLAUDE_FAST_MODEL,
        max_tokens=COMPARE_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        schema=COMPARE_SCHEMA,
        purpose="compare_listings",
        effort=llm.EFFORT_FAST,
    )

    llm.require_keys(data, tuple(COMPARE_SCHEMA["required"]), "compare_listings")
    if not isinstance(data["is_same"], bool):
        raise llm.InvalidOutputError("compare_listings: is_same must be true or false")
    if not isinstance(data["reasoning"], str):
        raise llm.InvalidOutputError("compare_listings: reasoning must be text")
    data["confidence"] = llm.require_unit_interval(data["confidence"], "confidence")
    return data


def _judge_pair(client, listing_a: Listing, listing_b: Listing, min_confidence: float) -> dict:
    """Decide whether two listings are the same property.

    Returns ``is_same``, ``confidence``, ``reasoning`` and ``decided_by`` (``"jev"`` or
    ``"fallback"``, i.e. Claude). Claude errors propagate.
    """
    key = pair_key(listing_a, listing_b)
    claude: dict = {}

    def claude_decides(pending):
        claude.update(_compare_pair(client, listing_a, listing_b))
        return {QUESTION_NAME: claude["is_same"] and claude["confidence"] >= min_confidence}

    state = {"a": judge.listing_state(listing_a), "b": judge.listing_state(listing_b)}
    result = judge.ask(
        JUDGMENT_KIND,
        key,
        state,
        [SAME_PROPERTY],
        claude_decides,
        fallback_model=settings.CLAUDE_FAST_MODEL,
    )
    judgment = result[QUESTION_NAME]

    if result.mode == "off":
        _record_off_mode_judgment(key, judgment)

    if judgment.decided_by == "jev":
        p_same = judgment.jev.probability
        return {
            "is_same": bool(judgment.value),
            "confidence": p_same if judgment.value else 1 - p_same,
            "reasoning": f"Jev: P(same property) = {p_same:.2f}",
            "decided_by": "jev",
        }
    return {
        "is_same": bool(judgment.value),
        "confidence": claude["confidence"],
        "reasoning": claude["reasoning"],
        "decided_by": "fallback",
    }


def _record_off_mode_judgment(key: str, judgment: judge.Judgment) -> None:
    """Write the JudgmentLog row judge.ask() does not write in off mode (the pair cache)."""
    JudgmentLog.objects.create(
        request_id=uuid.uuid4(),
        kind=JUDGMENT_KIND,
        subject_id=key,
        question=QUESTION_NAME,
        question_type=SAME_PROPERTY.type,
        mode="off",
        decided_by=judgment.decided_by,
        fallback_answer=judgment.fallback_value,
        jev_model=str(settings.JEV_MODEL).strip()[:64],
        fallback_model=str(settings.CLAUDE_FAST_MODEL)[:64],
    )


def _merge_clusters(clusters: list[ListingCluster]) -> ListingCluster:
    """Fold every cluster into the oldest one and delete the rest."""
    keep, *others = clusters
    with transaction.atomic():
        for other in others:
            keep.listings.add(*other.listings.all())
            keep.confidence = max(keep.confidence, other.confidence)
            if keep.canonical_id is None:
                keep.canonical_id = other.canonical_id
            other.delete()
        keep.save(update_fields=["confidence", "canonical", "updated_at"])
    return keep


def _file_match(
    listing_a: Listing, listing_b: Listing, verdict: dict
) -> tuple[ListingCluster, bool]:
    """Put a confirmed pair into one cluster. Returns the cluster and whether it is new."""
    clusters = list(
        ListingCluster.objects.filter(Q(listings=listing_a) | Q(listings=listing_b))
        .distinct()
        .order_by("created_at", "pk")
    )

    if not clusters:
        cluster = ListingCluster.objects.create(
            confidence=verdict["confidence"],
            reasoning=verdict["reasoning"],
            canonical=_pick_canonical(listing_a, listing_b),
        )
        cluster.listings.add(listing_a, listing_b)
        return cluster, True

    cluster = _merge_clusters(clusters) if len(clusters) > 1 else clusters[0]
    cluster.listings.add(listing_a, listing_b)
    cluster.confidence = max(cluster.confidence, verdict["confidence"])
    cluster.save(update_fields=["confidence", "updated_at"])
    return cluster, False


def run_dedup(radius_m: int = 50, min_confidence: float = 0.7) -> list[ListingCluster]:
    """Full dedup run: find candidates, judge up to the per-run cap, create or merge clusters.

    Comparison failures do not stop the run; they are counted and logged at the end. Pairs
    over ``DEDUP_MAX_PAIRS_PER_RUN`` wait for the next run.
    """
    candidates = find_candidates(radius_m=radius_m)
    cap = settings.DEDUP_MAX_PAIRS_PER_RUN
    if len(candidates) > cap:
        logger.info(
            "Dedup cap: judging %d of %d candidate pairs, the rest wait for the next run",
            cap,
            len(candidates),
        )
        candidates = candidates[:cap]

    created_clusters = []
    outcome = llm.BatchResult()
    client = get_client() if candidates else None

    for listing_a, listing_b in candidates:
        try:
            verdict = _judge_pair(client, listing_a, listing_b, min_confidence)
        except Exception as exc:
            outcome.add_failure(exc, context=f"Compare listings {listing_a.pk}/{listing_b.pk}")
            continue
        outcome.items.append((listing_a.pk, listing_b.pk))

        if not verdict["is_same"]:
            logger.debug(
                "Not a match: %s vs %s (%s, confidence=%.2f)",
                listing_a.pk,
                listing_b.pk,
                verdict["decided_by"],
                verdict["confidence"],
            )
            continue

        cluster, is_new = _file_match(listing_a, listing_b, verdict)
        if is_new:
            created_clusters.append(cluster)

        logger.info(
            "Matched: %s (%s) ↔ %s (%s) — %.0f%% by %s — %s",
            listing_a.pk,
            listing_a.source,
            listing_b.pk,
            listing_b.source,
            verdict["confidence"] * 100,
            verdict["decided_by"],
            verdict["reasoning"][:80],
        )

    outcome.log_summary("dedup comparisons")
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
