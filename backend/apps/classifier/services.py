from __future__ import annotations

import logging
from typing import Any

import anthropic
from django.conf import settings

from apps.classifier import llm
from apps.classifier.models import LearnedPreference, ListingEvaluation, UserFeedback
from apps.classifier.prompts import (
    LEARNED_PREFERENCES_BLOCK,
    PREFERENCE_EXTRACTION_PROMPT,
    SYSTEM_PROMPT,
)
from apps.listings.models import Listing

logger = logging.getLogger(__name__)

# max_tokens has to cover adaptive thinking as well as the answer, so these are
# deliberately generous. Cost is driven by what the model actually generates, not the cap.
CLASSIFY_MAX_TOKENS = 4096
EXTRACT_PREFERENCES_MAX_TOKENS = 2048
CLEANUP_MAX_TOKENS = 4096

VERDICTS = ["match", "review", "skip"]
MAX_PREFERENCE_PATTERNS = 3
MIN_PREFERENCE_WEIGHT = 0.5
MAX_PREFERENCE_WEIGHT = 2.0

# JSON schemas for structured outputs. The API enforces types, enums and required keys;
# it cannot enforce numeric min/max or array sizes, so those are checked in code below.
EVALUATE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": VERDICTS,
            "description": (
                "Overall verdict: match (buy candidate), "
                "review (needs closer look), skip (doesn't fit)"
            ),
        },
        "match_score": {
            "type": "number",
            "description": "How well the listing matches buyer criteria, from 0.0 to 1.0",
        },
        "summary": {
            "type": "string",
            "description": "2-3 sentence summary of the evaluation in Lithuanian",
        },
        "hard_filter_results": {
            "type": "object",
            "properties": {
                "price_ok": {"type": "boolean"},
                "type_ok": {"type": "boolean"},
                "area_ok": {"type": "boolean"},
                "plot_ok": {"type": "boolean"},
                "location_ok": {"type": "boolean"},
            },
            "required": [
                "price_ok",
                "type_ok",
                "area_ok",
                "plot_ok",
                "location_ok",
            ],
            "additionalProperties": False,
        },
        "quality_notes": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Observations about listing quality (photos, description completeness, etc.)"
            ),
        },
        "red_flags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Any suspicious or concerning aspects",
        },
    },
    "required": [
        "verdict",
        "match_score",
        "summary",
        "hard_filter_results",
        "quality_notes",
        "red_flags",
    ],
    "additionalProperties": False,
}

EXTRACT_PREFERENCES_SCHEMA = {
    "type": "object",
    "properties": {
        "patterns": {
            "type": "array",
            "description": "1 to 3 reusable preference patterns",
            "items": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Concise reusable preference pattern",
                    },
                    "weight": {
                        "type": "number",
                        "description": (
                            "Importance from 0.5 to 2.0: 0.5=minor, 1.0=normal, 2.0=strong"
                        ),
                    },
                },
                "required": ["pattern", "weight"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["patterns"],
    "additionalProperties": False,
}


def _build_system_prompt() -> str:
    base = SYSTEM_PROMPT
    prefs = list(
        LearnedPreference.objects.filter(is_active=True).values_list(
            "preference_type", "pattern", "weight"
        )
    )
    if not prefs:
        return base

    like_lines = [
        f"- {pattern} (weight: {weight})"
        for ptype, pattern, weight in prefs
        if ptype == LearnedPreference.PreferenceType.LIKE
    ]
    dislike_lines = [
        f"- {pattern} (weight: {weight})"
        for ptype, pattern, weight in prefs
        if ptype == LearnedPreference.PreferenceType.DISLIKE
    ]

    return base + LEARNED_PREFERENCES_BLOCK.format(
        like_patterns="\n".join(like_lines) or "None yet.",
        dislike_patterns="\n".join(dislike_lines) or "None yet.",
    )


def _listing_to_text(listing: Listing) -> str:
    parts = [
        f"**Title**: {listing.title}",
        f"**Source**: {listing.source} ({listing.source_url})",
        f"**Property type**: {listing.get_property_type_display()}",
        f"**Listing type**: {listing.get_listing_type_display()}",
        (
            f"**Price**: {listing.price} {listing.currency}"
            if listing.price
            else "**Price**: not specified"
        ),
    ]
    if listing.price_per_sqm:
        parts.append(f"**Price per kv.m**: {listing.price_per_sqm} EUR")
    if listing.area_sqm:
        parts.append(f"**Living area**: {listing.area_sqm} kv.m")
    if listing.plot_area_ares:
        parts.append(f"**Plot size**: {listing.plot_area_ares} arų")
    if listing.rooms:
        parts.append(f"**Rooms**: {listing.rooms}")
    if listing.floor:
        floor_str = f"{listing.floor}"
        if listing.total_floors:
            floor_str += f" / {listing.total_floors}"
        parts.append(f"**Floor**: {floor_str}")
    if listing.year_built:
        parts.append(f"**Year built**: {listing.year_built}")
    if listing.building_type:
        parts.append(f"**Building type**: {listing.get_building_type_display()}")
    if listing.heating_type:
        parts.append(f"**Heating**: {listing.heating_type}")
    if listing.energy_class:
        parts.append(f"**Energy class**: {listing.energy_class}")
    parts.append(f"**New construction**: {'Taip' if listing.is_new_construction else 'Ne'}")
    parts.append(f"**Address**: {listing.address_raw}")
    parts.append(f"**City**: {listing.city}")
    if listing.municipality:
        parts.append(f"**Municipality**: {listing.municipality}")
    if listing.district:
        parts.append(f"**District**: {listing.district}")
    if listing.cadastral_number:
        parts.append(f"**Cadastral number**: {listing.cadastral_number}")
    if listing.description:
        desc = listing.description[:1500]
        parts.append(f"\n**Description**:\n{desc}")
    photo_count = len(listing.photo_urls) if listing.photo_urls else 0
    parts.append(f"**Photos**: {photo_count} photo(s)")

    return "\n".join(parts)


def _get_client() -> anthropic.Anthropic:
    return llm.get_client()


def _validate_evaluation(data: dict) -> dict:
    """Check what the schema cannot: ranges and exact keys. Raises InvalidOutputError."""
    purpose = "evaluate_listing"
    llm.require_keys(data, tuple(EVALUATE_SCHEMA["required"]), purpose)
    if data["verdict"] not in VERDICTS:
        raise llm.InvalidOutputError(f"{purpose}: unknown verdict {data['verdict']!r}")
    score = llm.require_unit_interval(data["match_score"], "match_score")
    if not isinstance(data["summary"], str):
        raise llm.InvalidOutputError(f"{purpose}: summary must be text")

    hard_filters = data["hard_filter_results"]
    expected = EVALUATE_SCHEMA["properties"]["hard_filter_results"]["required"]
    if not isinstance(hard_filters, dict) or any(
        not isinstance(hard_filters.get(k), bool) for k in expected
    ):
        raise llm.InvalidOutputError(f"{purpose}: hard_filter_results is incomplete")

    for key in ("quality_notes", "red_flags"):
        value = data[key]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise llm.InvalidOutputError(f"{purpose}: {key} must be a list of text")

    return {**data, "match_score": score}


def classify_listing(
    listing: Listing, client: anthropic.Anthropic | None = None
) -> ListingEvaluation:
    """Classify a single listing using Claude and return the evaluation."""
    client = client or _get_client()
    system_prompt = _build_system_prompt()
    listing_text = _listing_to_text(listing)
    model = settings.CLAUDE_EVAL_MODEL

    data = llm.call_structured(
        client,
        model=model,
        max_tokens=CLASSIFY_MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": f"Evaluate this listing:\n\n{listing_text}",
            }
        ],
        schema=EVALUATE_SCHEMA,
        purpose="evaluate_listing",
        effort=llm.EFFORT_EVAL,
    )
    result = _validate_evaluation(data)

    evaluation, _ = ListingEvaluation.objects.update_or_create(
        listing=listing,
        defaults={
            "verdict": result["verdict"],
            "match_score": result["match_score"],
            "summary": result["summary"],
            "hard_filter_results": result["hard_filter_results"],
            "quality_notes": result["quality_notes"],
            "red_flags": result["red_flags"],
            "model_used": model,
        },
    )
    logger.info(
        "Classified listing %s: %s (%.0f%%)",
        listing.pk,
        evaluation.verdict,
        evaluation.match_score * 100,
    )
    return evaluation


def classify_batch(queryset: Any | None = None, limit: int = 50) -> llm.BatchResult:
    """Classify unclassified listings.

    Returns a BatchResult: ``items`` are the new evaluations, ``failed`` and ``error_counts``
    say how many listings errored and why. A missing API key raises immediately (clear
    ValueError) instead of failing once per listing.
    """
    client = _get_client()
    if queryset is None:
        queryset = Listing.objects.filter(is_active=True, evaluation__isnull=True)
    result = llm.BatchResult()
    for listing in queryset[:limit]:
        try:
            result.items.append(classify_listing(listing, client=client))
        except Exception as exc:
            result.add_failure(exc, context=f"Classify listing {listing.pk}")
    result.log_summary("classify_batch")
    return result


def process_feedback(
    listing: Listing, feedback_type: str, reason: str
) -> tuple[UserFeedback, list[LearnedPreference]]:
    """Record feedback and extract preference patterns from it."""
    feedback = UserFeedback.objects.create(
        listing=listing, feedback_type=feedback_type, reason=reason
    )
    preferences = extract_preferences(feedback)

    ListingEvaluation.objects.filter(listing=listing).delete()

    return feedback, preferences


def _validate_patterns(data: dict) -> list[tuple[str, float]]:
    purpose = "extract_preferences"
    llm.require_keys(data, ("patterns",), purpose)
    patterns = data["patterns"]
    if not isinstance(patterns, list) or not patterns:
        raise llm.InvalidOutputError(f"{purpose}: no patterns returned")

    cleaned = []
    for p in patterns[:MAX_PREFERENCE_PATTERNS]:
        text = p.get("pattern") if isinstance(p, dict) else None
        weight = p.get("weight") if isinstance(p, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise llm.InvalidOutputError(f"{purpose}: pattern must be non-empty text")
        if isinstance(weight, bool) or not isinstance(weight, int | float):
            raise llm.InvalidOutputError(f"{purpose}: weight must be a number")
        # Weight is advisory, so clamp rather than reject.
        weight = min(max(float(weight), MIN_PREFERENCE_WEIGHT), MAX_PREFERENCE_WEIGHT)
        cleaned.append((text.strip(), weight))
    return cleaned


def extract_preferences(feedback: UserFeedback) -> list[LearnedPreference]:
    """Use Claude to extract reusable preference patterns from feedback."""
    client = _get_client()
    listing = feedback.listing
    listing_text = _listing_to_text(listing)

    feedback_action = "LIKED" if feedback.feedback_type == "like" else "DISLIKED"

    prompt = PREFERENCE_EXTRACTION_PROMPT.format(
        feedback_action=feedback_action,
        reason=feedback.reason,
        listing_summary=listing_text,
    )

    data = llm.call_structured(
        client,
        model=settings.CLAUDE_FAST_MODEL,
        max_tokens=EXTRACT_PREFERENCES_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        schema=EXTRACT_PREFERENCES_SCHEMA,
        purpose="extract_preferences",
        effort=llm.EFFORT_FAST,
    )
    patterns = _validate_patterns(data)

    created = []
    for pattern, weight in patterns:
        pref = LearnedPreference.objects.create(
            preference_type=feedback.feedback_type,
            pattern=pattern,
            weight=weight,
            source_feedback=feedback,
        )
        created.append(pref)
        logger.info(
            "Extracted preference: [%s] %s (weight=%.1f)",
            feedback.feedback_type,
            pattern,
            weight,
        )

    return created


def cleanup_description(listing: Listing, client: anthropic.Anthropic | None = None) -> str:
    """Remove marketing fluff from a listing description using Claude.

    The description is only overwritten with complete, non-empty model output: a truncated
    or empty reply raises and leaves the stored description untouched.
    """
    from apps.classifier.prompts import CLEANUP_PROMPT

    if not listing.description or len(listing.description) < 20:
        return listing.description or ""

    client = client or _get_client()
    cleaned = llm.call_text(
        client,
        model=settings.CLAUDE_FAST_MODEL,
        max_tokens=CLEANUP_MAX_TOKENS,
        system=CLEANUP_PROMPT,
        messages=[{"role": "user", "content": listing.description[:3000]}],
        purpose="cleanup_description",
        effort=llm.EFFORT_FAST,
    )
    listing.description = cleaned
    listing.save(update_fields=["description"])
    logger.info("Cleaned description for listing %s", listing.pk)
    return cleaned


def cleanup_batch(limit: int = 50) -> llm.BatchResult:
    """Clean up descriptions for listings that haven't been cleaned yet.

    ``items`` holds the ids of cleaned listings; failures are counted and logged.
    """
    client = _get_client()
    uncleaned = Listing.objects.filter(
        is_active=True,
        description__regex=r"(!{2,}|SKUBIAI|PUIKI INVESTICIJA|Nepraleiskite)",
    )[:limit]
    result = llm.BatchResult()
    for listing in uncleaned:
        try:
            cleanup_description(listing, client=client)
            result.items.append(listing.pk)
        except Exception as exc:
            result.add_failure(exc, context=f"Cleanup listing {listing.pk}")
    result.log_summary("cleanup_batch")
    return result
