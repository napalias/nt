import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

import requests
from celery import shared_task
from django.conf import settings

from apps.listings.sources import Source, Target, crawl_command, get_source

logger = logging.getLogger(__name__)

STALE_MARKERS = ["nebepasiekiamas", "skelbimas nerastas", "pašalintas iš sąrašo"]

BACKUP_DIR = Path("/app/backups")


@shared_task
def run_spider(name: str, property_type: str | None = None, **spider_args: object) -> dict:
    """Run one registry source (see apps/listings/sources.py). Triggered from celery-beat.

    Spider args, delay and timeout come from the registry; keyword args here override
    registry spider args (e.g. `run_spider("domoplius", max_pages=1)`). With no
    `property_type`, every target switched on in settings.SCAN_PROPERTY_TYPES runs, one
    crawl (and one ScrapeRun) each. Runs on the scraper worker, scrapers project at /app.
    """
    source = get_source(name)
    if not source.built:
        raise ValueError(f"Source {name!r} is registered but its spider is not built yet")
    if property_type is None:
        targets = source.active_targets(settings.SCAN_PROPERTY_TYPES)
    else:
        targets = (source.target(property_type),)

    if not targets:
        logger.warning(
            "Source %s has no target for SCAN_PROPERTY_TYPES=%s; nothing to crawl",
            name,
            settings.SCAN_PROPERTY_TYPES,
        )
    crawls = [_crawl(source, target, spider_args) for target in targets]
    return {"spider": name, "crawls": crawls}


def _crawl(source: Source, target: Target, overrides: dict[str, object]) -> dict:
    cmd = crawl_command(source, target, overrides)
    logger.info("Starting spider: %s", " ".join(cmd))
    report: dict = {"property_type": target.property_type, "timed_out": False}
    try:
        result = subprocess.run(
            cmd, cwd="/app", capture_output=True, text=True, timeout=source.timeout_s
        )
    except subprocess.TimeoutExpired:
        # The killed crawl never closes its ScrapeRun; the recorder marks it interrupted
        # on a later run once it is STALE_AFTER_H old.
        logger.error("Spider %s timed out after %d s", source.spider, source.timeout_s)
        return {
            **report,
            "timed_out": True,
            "exit_code": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }

    if result.returncode != 0:
        logger.error(
            "Spider %s failed (exit %d): %s",
            source.spider,
            result.returncode,
            result.stderr[-500:],
        )
    else:
        logger.info("Spider %s completed successfully", source.spider)
    return {
        **report,
        "exit_code": result.returncode,
        "stdout_tail": result.stdout[-200:] if result.stdout else "",
        "stderr_tail": result.stderr[-200:] if result.stderr else "",
    }


@shared_task
def nightly_db_backup() -> dict:
    """Dump the PostgreSQL database to a timestamped file in /app/backups/.

    Uses pg_dump with the DATABASE_URL from Django settings.
    Keeps the last 7 backups and removes older ones.
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    db_settings = settings.DATABASES["default"]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"realestate_{timestamp}.sql.gz"
    filepath = BACKUP_DIR / filename

    env = {
        "PGPASSWORD": db_settings.get("PASSWORD", ""),
    }

    cmd = (
        f"pg_dump"
        f" -h {db_settings['HOST']}"
        f" -p {db_settings.get('PORT', '5432')}"
        f" -U {db_settings['USER']}"
        f" -d {db_settings['NAME']}"
        f" --no-owner --no-acl"
        f" | gzip > {filepath}"
    )

    logger.info("Starting database backup to %s", filepath)

    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=1800,
            env={**subprocess.os.environ, **env},
        )

        if result.returncode != 0:
            logger.error("Database backup failed: %s", result.stderr[-500:])
            return {"status": "error", "message": result.stderr[-500:]}

        size_mb = round(filepath.stat().st_size / (1024 * 1024), 2)
        logger.info("Database backup completed: %s (%.2f MB)", filename, size_mb)

        # Rotate: keep last 7 backups
        backups = sorted(BACKUP_DIR.glob("realestate_*.sql.gz"))
        for old_backup in backups[:-7]:
            old_backup.unlink()
            logger.info("Removed old backup: %s", old_backup.name)

        return {"status": "ok", "file": filename, "size_mb": size_mb}
    except subprocess.TimeoutExpired:
        logger.error("Database backup timed out after 30 minutes")
        return {"status": "error", "message": "Backup timed out"}
    except Exception as exc:
        logger.exception("Database backup failed with unexpected error")
        return {"status": "error", "message": str(exc)}


@shared_task
def check_listing_validity(batch_size: int = 50) -> dict:
    """Check if active listings are still available on their source sites.

    Makes GET requests to each listing's source_url. If the response is 404/410
    or the page body contains Lithuanian "unavailable" markers, the listing is
    marked as is_active=False.

    Processes the oldest-seen listings first so the whole table is checked over
    successive runs.
    """
    from apps.listings.models import Listing

    listings = list(
        Listing.objects.filter(is_active=True)
        .order_by("last_seen_at")[:batch_size]
        .values_list("pk", "source_url")
    )

    deactivated = 0
    for pk, source_url in listings:
        try:
            resp = requests.get(
                source_url,
                timeout=10,
                allow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (compatible; NTBot/1.0)"},
            )
            if resp.status_code in (404, 410):
                Listing.objects.filter(pk=pk).update(is_active=False)
                deactivated += 1
            elif resp.status_code == 200:
                body_lower = resp.text.lower()
                if any(marker in body_lower for marker in STALE_MARKERS):
                    Listing.objects.filter(pk=pk).update(is_active=False)
                    deactivated += 1
        except requests.RequestException:
            pass  # network error — skip, will retry next run

        # Be polite: 1s delay between requests
        time.sleep(1)

    logger.info("Validity check: %d/%d deactivated", deactivated, len(listings))
    return {"checked": len(listings), "deactivated": deactivated}
