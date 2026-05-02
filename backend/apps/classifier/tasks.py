import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def classify_new_listings(limit: int = 20):
    """Classify unclassified active listings. Scheduled via celery-beat."""
    from apps.classifier.services import classify_batch

    results = classify_batch(limit=limit)
    summary: dict[str, int] = {}
    for e in results:
        summary[e.verdict] = summary.get(e.verdict, 0) + 1
    logger.info("Auto-classified %d listings: %s", len(results), summary)
    return {"classified": len(results), "by_verdict": summary}


@shared_task
def reclassify_all():
    """Re-classify all active listings (e.g. after criteria change)."""
    from apps.classifier.models import ListingEvaluation
    from apps.classifier.services import classify_listing
    from apps.listings.models import Listing

    listings = list(Listing.objects.filter(is_active=True))
    total = 0
    for listing in listings:
        ListingEvaluation.objects.filter(listing=listing).delete()
        try:
            classify_listing(listing)
            total += 1
        except Exception:
            logger.exception("Failed to reclassify listing %s", listing.pk)
    logger.info("Re-classified %d / %d listings", total, len(listings))
    return {"reclassified": total, "total": len(listings)}


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

    count = cleanup_batch(limit=limit)
    logger.info("Cleaned %d listing descriptions", count)
    return {"cleaned": count}
