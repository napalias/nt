import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def classify_new_listings(limit: int = 20):
    """Classify unclassified active listings. Scheduled via celery-beat."""
    from apps.classifier.services import classify_batch

    result = classify_batch(limit=limit)
    summary: dict[str, int] = {}
    for e in result.items:
        summary[e.verdict] = summary.get(e.verdict, 0) + 1
    logger.info("Auto-classified %d listings: %s", len(result.items), summary)
    return {
        "classified": len(result.items),
        "failed": result.failed,
        "errors": result.error_counts,
        "by_verdict": summary,
    }


@shared_task
def reclassify_all():
    """Re-classify all active listings (e.g. after criteria change)."""
    from apps.classifier.llm import BatchResult
    from apps.classifier.services import _get_client, classify_listing
    from apps.listings.models import Listing

    client = _get_client()
    listings = list(Listing.objects.filter(is_active=True))
    result = BatchResult()
    for listing in listings:
        # Keep the old evaluation until the new one is saved: a failed call must not
        # leave the listing with no evaluation at all.
        try:
            classify_listing(listing, client=client)
            result.items.append(listing.pk)
        except Exception as exc:
            result.add_failure(exc, context=f"Reclassify listing {listing.pk}")
    result.log_summary("reclassify_all")
    return {
        "reclassified": len(result.items),
        "failed": result.failed,
        "errors": result.error_counts,
        "total": len(listings),
    }


@shared_task
def cluster_listings():
    """Nightly task: find cross-portal duplicates using AI comparison."""
    from apps.classifier.dedup import run_dedup

    clusters = run_dedup()
    logger.info("Dedup created %d new clusters", len(clusters))
    return {"new_clusters": len(clusters)}


@shared_task
def cleanup_descriptions(limit: int = 50):
    """Clean marketing fluff from listing descriptions using AI."""
    from apps.classifier.services import cleanup_batch

    result = cleanup_batch(limit=limit)
    logger.info("Cleaned %d listing descriptions", len(result.items))
    return {
        "cleaned": len(result.items),
        "failed": result.failed,
        "errors": result.error_counts,
    }
