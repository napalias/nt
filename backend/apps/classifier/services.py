from __future__ import annotations

import logging
from typing import Any

import anthropic
from django.conf import settings

from apps.classifier.models import LearnedPreference, ListingEvaluation, UserFeedback
from apps.classifier.prompts import (
    LEARNED_PREFERENCES_BLOCK,
    PREFERENCE_EXTRACTION_PROMPT,
    SYSTEM_PROMPT,
)
from apps.listings.models import Listing

logger = logging.getLogger(__name__)

CLASSIFY_MODEL = "claude-sonnet-4-20250514"
EXTRACT_MODEL = "claude-haiku-4-5-20251001"

EVALUATE_TOOL = {
    "name": "evaluate_listing",
    "description": "Submit a structured evaluation of a real estate listing",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["match", "review", "skip"],
                "description": (
                    "Overall verdict: match (buy candidate), "
                    "review (needs closer look), skip (doesn't fit)"
                ),
            },
            "match_score": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "description": "How well the listing matches buyer criteria (0.0-1.0)",
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
    },
}

EXTRACT_PREFERENCE_TOOL = {
    "name": "extract_preferences",
    "description": "Extract reusable preference patterns from buyer feedback",
    "input_schema": {
        "type": "object",
        "properties": {
            "patterns": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Concise reusable preference pattern",
                        },
                        "weight": {
                            "type": "number",
                            "minimum": 0.5,
                            "maximum": 2.0,
                            "description": ("Importance: 0.5=minor, 1.0=normal, 2.0=strong"),
                        },
                    },
                    "required": ["pattern", "weight"],
                },
                "minItems": 1,
                "maxItems": 3,
            },
        },
        "required": ["patterns"],
    },
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
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file to use AI classification."
        )
    return anthropic.Anthropic(api_key=api_key)


def classify_listing(listing: Listing) -> ListingEvaluation:
    """Classify a single listing using Claude and return the evaluation."""
    client = _get_client()
    system_prompt = _build_system_prompt()
    listing_text = _listing_to_text(listing)

    response = client.messages.create(
        model=CLASSIFY_MODEL,
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        tools=[EVALUATE_TOOL],
        tool_choice={"type": "tool", "name": "evaluate_listing"},
        messages=[
            {
                "role": "user",
                "content": f"Evaluate this listing:\n\n{listing_text}",
            }
        ],
    )

    tool_input = _extract_tool_input(response, "evaluate_listing")

    evaluation, _ = ListingEvaluation.objects.update_or_create(
        listing=listing,
        defaults={
            "verdict": tool_input["verdict"],
            "match_score": tool_input["match_score"],
            "summary": tool_input["summary"],
            "hard_filter_results": tool_input["hard_filter_results"],
            "quality_notes": tool_input.get("quality_notes", []),
            "red_flags": tool_input.get("red_flags", []),
            "model_used": CLASSIFY_MODEL,
        },
    )
    logger.info(
        "Classified listing %s: %s (%.0f%%)",
        listing.pk,
        evaluation.verdict,
        evaluation.match_score * 100,
    )
    return evaluation


def classify_batch(queryset: Any | None = None, limit: int = 50) -> list[ListingEvaluation]:
    """Classify unclassified listings. Returns list of new evaluations."""
    if queryset is None:
        queryset = Listing.objects.filter(is_active=True, evaluation__isnull=True)
    listings = queryset[:limit]
    results = []
    for listing in listings:
        try:
            evaluation = classify_listing(listing)
            results.append(evaluation)
        except Exception:
            logger.exception("Failed to classify listing %s", listing.pk)
    return results


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

    response = client.messages.create(
        model=EXTRACT_MODEL,
        max_tokens=512,
        tools=[EXTRACT_PREFERENCE_TOOL],
        tool_choice={"type": "tool", "name": "extract_preferences"},
        messages=[{"role": "user", "content": prompt}],
    )

    tool_input = _extract_tool_input(response, "extract_preferences")
    created = []
    for p in tool_input["patterns"]:
        pref = LearnedPreference.objects.create(
            preference_type=feedback.feedback_type,
            pattern=p["pattern"],
            weight=p.get("weight", 1.0),
            source_feedback=feedback,
        )
        created.append(pref)
        logger.info(
            "Extracted preference: [%s] %s (weight=%.1f)",
            feedback.feedback_type,
            p["pattern"],
            p.get("weight", 1.0),
        )

    return created


def _extract_tool_input(response: anthropic.types.Message, tool_name: str) -> dict:
    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            return block.input
    raise ValueError(f"No {tool_name} tool call found in response")
