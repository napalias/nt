from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from typing import Any

import anthropic
from django.conf import settings

from apps.classifier import criteria, judge, llm
from apps.classifier.criteria import FilterResult, Status
from apps.classifier.judge import Question
from apps.classifier.models import LearnedPreference, ListingEvaluation, UserFeedback
from apps.classifier.prompts import (
    LEARNED_PREFERENCES_BLOCK,
    NARRATIVE_SYSTEM_PROMPT,
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

# What Claude writes once code has fixed the verdict and score. No verdict, no score, no
# filter booleans: those are computed in criteria.py.
NARRATIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "2-3 sentence summary of the listing and its fit, in Lithuanian",
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
    "required": ["summary", "quality_notes", "red_flags"],
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


def _build_system_prompt(base: str = SYSTEM_PROMPT) -> str:
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


def _validate_narrative(data: dict) -> dict:
    """Check what the schema cannot. Extra keys (a verdict, a score) are ignored: code decides."""
    purpose = "evaluate_narrative"
    llm.require_keys(data, tuple(NARRATIVE_SCHEMA["required"]), purpose)
    summary = data["summary"]
    if not isinstance(summary, str) or not summary.strip():
        raise llm.InvalidOutputError(f"{purpose}: summary must be non-empty text")
    for key in ("quality_notes", "red_flags"):
        value = data[key]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise llm.InvalidOutputError(f"{purpose}: {key} must be a list of text")
    return {k: data[k] for k in NARRATIVE_SCHEMA["required"]}


# --- The evaluation pipeline ---------------------------------------------------------------
#
# 1. Code filters (criteria.py): price, area, plot, location. Pure, tested, unknown when a
#    value is missing.
# 2. Two Jev yes/no judgments through judge.py: new build, part of a house. The keyword path
#    below decides when Jev is off, unsure or unavailable.
# 3. Verdict and score from 1 and 2, in code. A hard fail ends here: skip, no Claude call.
# 4. Everything else gets a Claude narrative (summary, quality notes, red flags) on
#    CLAUDE_EVAL_MODEL. Claude cannot change the verdict or the score.
#
# JEV_MODE=shadow keeps the old single Claude evaluation as the saved result and compares the
# code filters with it, per filter, in the log.

JUDGE_KIND = "evaluation"
IS_NEW_BUILD = Question.yes_no("is_new_build", criteria.NEW_BUILD_QUESTION)
IS_PART_OF_HOUSE = Question.yes_no("is_part_of_house", criteria.PART_OF_HOUSE_QUESTION)
JUDGMENTS = (IS_NEW_BUILD, IS_PART_OF_HOUSE)
CODE_MODEL = "code"


def _keyword_judgments(listing: Listing, pending: Sequence[Question]) -> dict[str, Any]:
    """The decider that needs no model: the portal's own fields and Lithuanian keywords. It
    abstains (None) when they do not settle a question."""
    text = f"{listing.title}\n{listing.description}"
    answers = {
        IS_NEW_BUILD.name: criteria.keyword_new_build(
            bool(listing.is_new_construction), listing.year_built, text, date.today().year
        ),
        IS_PART_OF_HOUSE.name: criteria.keyword_part_of_house(listing.property_type, text),
    }
    return {q.name: answers[q.name] for q in pending}


def _code_filters(listing: Listing) -> dict[str, FilterResult]:
    point = listing.location
    return {
        "price": criteria.check_price(listing.price, listing.currency),
        "area": criteria.check_area(listing.area_sqm),
        "plot": criteria.check_plot(listing.plot_area_ares),
        # location_precision arrives with the contact-scrub change; absent means usable.
        "location": criteria.check_location(
            point.y if point else None,
            point.x if point else None,
            [listing.city, listing.district, listing.address_raw],
            getattr(listing, "location_precision", None),
        ),
    }


def run_filters(listing: Listing) -> dict[str, FilterResult]:
    """All six checks for a listing: four code filters and the two type judgments."""
    results = _code_filters(listing)
    is_part_property = listing.property_type == Listing.PropertyType.PART_OF_HOUSE
    settled = is_part_property or any(r.status == Status.FAIL for r in results.values())

    def keyword(pending: Sequence[Question]) -> dict[str, Any]:
        return _keyword_judgments(listing, pending)

    if settled and judge.current_mode() != "shadow":
        # Already a hard fail: Jev's answers could not change the verdict, so do not pay for them.
        values = keyword(JUDGMENTS)
    else:
        values = judge.ask(
            JUDGE_KIND,
            listing.pk,
            judge.listing_state(listing),
            JUDGMENTS,
            keyword,
            fallback_model="keyword",
        ).values
    part_of_house = True if is_part_property else values[IS_PART_OF_HOUSE.name]
    results["new_build"] = criteria.type_result(values[IS_NEW_BUILD.name], "new_build")
    results["whole_house"] = criteria.type_result(part_of_house, "whole_house")
    return results


def _narrative_request(
    listing: Listing, filters: dict[str, FilterResult], decision: criteria.Decision
) -> str:
    checks = "\n".join(
        f"- {name}: {filters[name].status.value} ({filters[name].detail})"
        for name in criteria.FILTER_ORDER
    )
    return (
        f"Evaluate this listing. Verdict, decided in code: {decision.verdict}.\n\n"
        f"Checks:\n{checks}\n\n{_listing_to_text(listing)}"
    )


def _narrate(
    listing: Listing,
    filters: dict[str, FilterResult],
    decision: criteria.Decision,
    client: anthropic.Anthropic,
) -> dict:
    data = llm.call_structured(
        client,
        model=settings.CLAUDE_EVAL_MODEL,
        max_tokens=CLASSIFY_MAX_TOKENS,
        system=[
            {
                "type": "text",
                "text": _build_system_prompt(NARRATIVE_SYSTEM_PROMPT),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": _narrative_request(listing, filters, decision)}],
        schema=NARRATIVE_SCHEMA,
        purpose="evaluate_narrative",
        effort=llm.EFFORT_EVAL,
    )
    return _validate_narrative(data)


def _save_evaluation(
    listing: Listing,
    *,
    verdict: str,
    score: float,
    summary: str,
    hard_filter_results: dict,
    quality_notes: list[str],
    red_flags: list[str],
    model: str,
) -> ListingEvaluation:
    evaluation, _ = ListingEvaluation.objects.update_or_create(
        listing=listing,
        defaults={
            "verdict": verdict,
            "match_score": score,
            "summary": summary,
            "hard_filter_results": hard_filter_results,
            "quality_notes": quality_notes,
            "red_flags": red_flags,
            "model_used": model,
        },
    )
    logger.info(
        "Classified listing %s: %s (%.0f%%) by %s",
        listing.pk,
        evaluation.verdict,
        evaluation.match_score * 100,
        model,
    )
    return evaluation


def classify_listing(
    listing: Listing, client: anthropic.Anthropic | None = None
) -> ListingEvaluation:
    """Evaluate one listing and save the result. See the pipeline notes above.

    A hard fail is saved as a skip without any Claude call, and without needing a client.
    """
    if judge.current_mode() == "shadow":
        return _classify_shadow(listing, client)

    filters = run_filters(listing)
    decision = criteria.decide(filters)
    if decision.verdict == "skip":
        return _save_evaluation(
            listing,
            verdict=decision.verdict,
            score=decision.score,
            summary=decision.summary,
            hard_filter_results=decision.hard_filter_results,
            quality_notes=[],
            red_flags=[],
            model=CODE_MODEL,
        )

    narrative = _narrate(listing, filters, decision, client or _get_client())
    notes = list(narrative["quality_notes"])
    if decision.unknown:
        unknown = "; ".join(filters[n].detail for n in decision.unknown)
        notes.insert(0, f"Nežinoma: {unknown}")
    return _save_evaluation(
        listing,
        verdict=decision.verdict,
        score=decision.score,
        summary=narrative["summary"],
        hard_filter_results=decision.hard_filter_results,
        quality_notes=notes,
        red_flags=narrative["red_flags"],
        model=settings.CLAUDE_EVAL_MODEL,
    )


def _classify_shadow(listing: Listing, client: anthropic.Anthropic | None) -> ListingEvaluation:
    """Shadow: the old single Claude evaluation decides and is saved, as shadow means for every
    judgment. The code filters and Jev run beside it and agreement is logged per filter."""
    filters = run_filters(listing)
    result = _evaluate_full(listing, client or _get_client())
    log_shadow_agreement(listing, filters, result["hard_filter_results"])
    return _save_evaluation(
        listing,
        verdict=result["verdict"],
        score=result["match_score"],
        summary=result["summary"],
        hard_filter_results=result["hard_filter_results"],
        quality_notes=result["quality_notes"],
        red_flags=result["red_flags"],
        model=settings.CLAUDE_EVAL_MODEL,
    )


HARD_FILTER_KEYS = ("price_ok", "type_ok", "area_ok", "plot_ok", "location_ok")


def log_shadow_agreement(
    listing: Listing, filters: dict[str, FilterResult], claude: dict[str, bool]
) -> dict[str, bool | None]:
    """One log line per filter: the code's answer, the old Claude evaluation's, and whether they
    agree (None when the code could not tell). Disagreements log at WARNING."""
    code = criteria.hard_filter_results(filters)
    agreement: dict[str, bool | None] = {}
    for key in HARD_FILTER_KEYS:
        code_value = code.get(key)
        agree = None if code_value is None else code_value == claude[key]
        agreement[key] = agree
        log = logger.warning if agree is False else logger.info
        log(
            "evaluation shadow filter=%s listing=%s code=%s claude=%s agree=%s",
            key,
            listing.pk,
            "unknown" if code_value is None else code_value,
            claude[key],
            agree,
        )
    return agreement


def _evaluate_full(listing: Listing, client: anthropic.Anthropic) -> dict:
    """The old single-call evaluation: Claude checks the filters, scores and writes the notes.
    Returns the validated answer without saving it. Runs in shadow mode only."""
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
    return _validate_evaluation(data)


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

    # Clean from the portal's text (description_raw) when we have it, so cleaning is
    # repeatable; rows scraped before description_raw existed fall back to description.
    source_text = listing.description_raw or listing.description
    if not source_text or len(source_text) < 20:
        return listing.description or ""

    client = client or _get_client()
    cleaned = llm.call_text(
        client,
        model=settings.CLAUDE_FAST_MODEL,
        max_tokens=CLEANUP_MAX_TOKENS,
        system=CLEANUP_PROMPT,
        messages=[{"role": "user", "content": source_text[:3000]}],
        purpose="cleanup_description",
        effort=llm.EFFORT_FAST,
    )
    # The scraper keeps description_clean (and description) across re-scrapes while
    # description_raw is unchanged, so the cleanup is not undone by the next scrape.
    if not listing.description_raw:
        listing.description_raw = source_text
    listing.description_clean = cleaned
    listing.description = cleaned
    listing.save(update_fields=["description", "description_raw", "description_clean"])
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
