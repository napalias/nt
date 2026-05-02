from __future__ import annotations

from typing import Literal

from django.shortcuts import get_object_or_404
from ninja import Router, Schema

from apps.classifier.dedup import run_dedup
from apps.classifier.models import (
    LearnedPreference,
    ListingCluster,
    ListingEvaluation,
    UserFeedback,
)
from apps.classifier.services import (
    classify_batch,
    classify_listing,
    cleanup_description,
    process_feedback,
)
from apps.classifier.services import cleanup_batch as _cleanup_batch
from apps.listings.models import Listing

router = Router(tags=["classifier"])

MAX_LIMIT = 200


# --- Schemas ---


class EvaluationOut(Schema):
    listing_id: int
    listing_title: str
    verdict: str
    match_score: float
    summary: str
    hard_filter_results: dict
    quality_notes: list[str]
    red_flags: list[str]
    classified_at: str
    model_used: str


class FeedbackIn(Schema):
    feedback_type: Literal["like", "dislike"]
    reason: str


class FeedbackOut(Schema):
    id: int
    listing_id: int
    feedback_type: str
    reason: str
    extracted_preferences: list[str]


class FeedbackListOut(Schema):
    id: int
    listing_id: int
    listing_title: str
    feedback_type: str
    reason: str
    created_at: str


class PreferenceOut(Schema):
    id: int
    preference_type: str
    pattern: str
    weight: float
    is_active: bool


class ClassifyBatchIn(Schema):
    limit: int = 50


class ClassifyBatchOut(Schema):
    classified_count: int
    evaluations: list[EvaluationOut]


# --- Endpoints ---


@router.post("/classify/batch", response=ClassifyBatchOut)
def classify_many(request, body: ClassifyBatchIn):
    """Classify unclassified active listings in batch."""
    limit = min(body.limit, MAX_LIMIT)
    evaluations = classify_batch(limit=limit)
    return {
        "classified_count": len(evaluations),
        "evaluations": [_evaluation_to_out(e) for e in evaluations],
    }


@router.post("/classify/{listing_id}", response=EvaluationOut)
def classify_single(request, listing_id: int):
    """Classify a single listing using AI."""
    listing = get_object_or_404(Listing, pk=listing_id)
    evaluation = classify_listing(listing)
    return _evaluation_to_out(evaluation)


@router.post("/feedback/{listing_id}", response=FeedbackOut)
def submit_feedback(request, listing_id: int, body: FeedbackIn):
    """Submit like/dislike feedback for a listing."""
    listing = get_object_or_404(Listing, pk=listing_id)
    feedback, preferences = process_feedback(listing, body.feedback_type, body.reason)
    return {
        "id": feedback.id,
        "listing_id": listing.id,
        "feedback_type": feedback.feedback_type,
        "reason": feedback.reason,
        "extracted_preferences": [p.pattern for p in preferences],
    }


@router.get("/evaluated", response=list[EvaluationOut])
def list_evaluated(
    request,
    verdict: str | None = None,
    min_score: float | None = None,
    limit: int = 100,
):
    """List evaluated listings, optionally filtered."""
    limit = min(limit, MAX_LIMIT)
    qs = ListingEvaluation.objects.select_related("listing")
    if verdict:
        qs = qs.filter(verdict=verdict)
    if min_score is not None:
        qs = qs.filter(match_score__gte=min_score)
    return [_evaluation_to_out(e) for e in qs[:limit]]


@router.get("/preferences", response=list[PreferenceOut])
def list_preferences(request, active_only: bool = True):
    """List learned preferences."""
    qs = LearnedPreference.objects.all()
    if active_only:
        qs = qs.filter(is_active=True)
    return list(qs.values("id", "preference_type", "pattern", "weight", "is_active"))


@router.delete("/preferences/{preference_id}")
def delete_preference(request, preference_id: int):
    """Deactivate a learned preference."""
    pref = get_object_or_404(LearnedPreference, pk=preference_id)
    pref.is_active = False
    pref.save(update_fields=["is_active"])
    return {"ok": True}


@router.get("/feedback", response=list[FeedbackListOut])
def list_feedback(request, limit: int = 50):
    """List recent feedback."""
    limit = min(limit, MAX_LIMIT)
    qs = UserFeedback.objects.select_related("listing")[:limit]
    return [
        {
            "id": f.id,
            "listing_id": f.listing_id,
            "listing_title": f.listing.title,
            "feedback_type": f.feedback_type,
            "reason": f.reason,
            "created_at": f.created_at.isoformat(),
        }
        for f in qs
    ]


@router.post("/cleanup/{listing_id}")
def cleanup_single(request, listing_id: int):
    """Clean marketing fluff from a listing's description using AI."""
    listing = get_object_or_404(Listing, pk=listing_id)
    cleaned = cleanup_description(listing)
    return {"listing_id": listing.id, "cleaned_description": cleaned}


@router.post("/cleanup/batch")
def cleanup_many(request):
    """Clean descriptions for listings with marketing fluff."""
    count = _cleanup_batch(limit=50)
    return {"cleaned_count": count}


class ClusterOut(Schema):
    id: int
    listing_ids: list[int]
    listing_titles: list[str]
    sources: list[str]
    confidence: float
    reasoning: str
    canonical_id: int | None


@router.post("/dedup", response=list[ClusterOut])
def run_dedup_endpoint(request):
    """Run AI-powered cross-portal duplicate detection."""
    clusters = run_dedup()
    return [_cluster_to_out(c) for c in clusters]


@router.get("/clusters", response=list[ClusterOut])
def list_clusters(request, limit: int = 100):
    """List detected duplicate clusters."""
    limit = min(limit, MAX_LIMIT)
    qs = ListingCluster.objects.prefetch_related("listings")[:limit]
    return [_cluster_to_out(c) for c in qs]


def _cluster_to_out(c: ListingCluster) -> dict:
    listings = list(c.listings.all())
    return {
        "id": c.id,
        "listing_ids": [lst.id for lst in listings],
        "listing_titles": [lst.title for lst in listings],
        "sources": [lst.source for lst in listings],
        "confidence": c.confidence,
        "reasoning": c.reasoning,
        "canonical_id": c.canonical_id,
    }


def _evaluation_to_out(e: ListingEvaluation) -> dict:
    return {
        "listing_id": e.listing_id,
        "listing_title": e.listing.title,
        "verdict": e.verdict,
        "match_score": e.match_score,
        "summary": e.summary,
        "hard_filter_results": e.hard_filter_results,
        "quality_notes": e.quality_notes,
        "red_flags": e.red_flags,
        "classified_at": e.classified_at.isoformat(),
        "model_used": e.model_used,
    }
