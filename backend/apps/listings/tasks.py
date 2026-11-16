import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.classifier import ingest
from apps.listings.models import ScrapeRun
from apps.listings.scrape_runs import RUN_ID_SETTING, clip_start_url
from apps.listings.scrub import scrub_text
from apps.listings.sources import Source, Target, crawl_command, get_source

logger = logging.getLogger(__name__)

STALE_MARKERS = ["nebepasiekiamas", "skelbimas nerastas", "pašalintas iš sąrašo"]

BACKUP_DIR = Path("/app/backups")

# How much of a crawl's log the row keeps.
LOG_TAIL_LINES = 200
LOG_TAIL_CHARS = 20_000


@shared_task
def run_spider(
    name: str,
    property_type: str | None = None,
    trigger: str = ScrapeRun.Trigger.MANUAL,
    **spider_args: object,
) -> dict:
    """Run one registry source (see apps/listings/sources.py). Triggered from celery-beat.

    Spider args, delay and timeout come from the registry; keyword args here override
    registry spider args (e.g. `run_spider("domoplius", max_pages=1)`). With no
    `property_type`, every target switched on in settings.SCAN_PROPERTY_TYPES runs, one
    crawl (and one ScrapeRun) each. `trigger` is "beat" from the schedule (config/celery.py)
    and "manual" otherwise; it is recorded, not passed to the spider. Runs on the scraper
    worker, scrapers project at /app.
    """
    if trigger not in ScrapeRun.Trigger.values:
        raise ValueError(f"Unknown trigger {trigger!r}; use one of {ScrapeRun.Trigger.values}")
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
    crawls = [_crawl(source, target, spider_args, trigger) for target in targets]
    return {"spider": name, "crawls": crawls}


def _crawl(source: Source, target: Target, overrides: dict[str, object], trigger: str) -> dict:
    """One `scrapy crawl` subprocess. The row exists before the crawl starts, so a crawl
    that dies early, crashes or times out still leaves a failed run with its log."""
    run = ScrapeRun.objects.create(
        spider=source.spider,
        started_at=timezone.now(),
        trigger=trigger,
        args=source.crawl_args(target, overrides),
        start_url=clip_start_url(target.start_urls[0]),  # Target guarantees one
    )
    cmd = [*crawl_command(source, target, overrides), "-s", f"{RUN_ID_SETTING}={run.pk}"]
    logger.info("Starting spider (run %d): %s", run.pk, " ".join(cmd))
    report: dict = {"property_type": target.property_type, "run_id": run.pk, "timed_out": False}
    try:
        result = subprocess.run(
            cmd, cwd="/app", capture_output=True, text=True, timeout=source.timeout_s
        )
    except subprocess.TimeoutExpired as exc:
        # The killed crawl never reached spider_closed; close its row here and now.
        logger.error("Spider %s timed out after %d s", source.spider, source.timeout_s)
        _close_run(run.pk, _text(exc.stderr) or _text(exc.output), "timeout")
        return {
            **report,
            "timed_out": True,
            "exit_code": None,
            "stdout_tail": "",
            "stderr_tail": "",
        }
    except BaseException as exc:
        # No crawl ran (scrapy missing, fork failed) or the worker is shutting down: close
        # the row as failed now rather than leave it running for the 6 h stale sweep.
        _close_run(run.pk, "", f"error: {type(exc).__name__}")
        raise

    if result.returncode != 0:
        logger.error(
            "Spider %s failed (exit %d): %s",
            source.spider,
            result.returncode,
            result.stderr[-500:],
        )
        unclosed_reason = f"exit {result.returncode}"
    else:
        logger.info("Spider %s completed successfully", source.spider)
        # Exit 0, yet the recorder never closed the row: the outcome is unknown.
        unclosed_reason = "unrecorded"
    # Scrapy logs to stderr; stdout only carries what a spider prints.
    _close_run(run.pk, result.stderr or result.stdout or "", unclosed_reason)
    return {
        **report,
        "exit_code": result.returncode,
        "stdout_tail": result.stdout[-200:] if result.stdout else "",
        "stderr_tail": result.stderr[-200:] if result.stderr else "",
    }


def _text(output: str | bytes | None) -> str:
    """TimeoutExpired carries bytes even with text=True; decode whatever it holds."""
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return output or ""


def log_tail(log: str) -> str:
    """The last lines of a crawl log, contact data scrubbed (dropped-item warnings print the
    item), at most LOG_TAIL_LINES lines and LOG_TAIL_CHARS characters."""
    tail = "\n".join(log.splitlines()[-LOG_TAIL_LINES:])
    return scrub_text(tail)[-LOG_TAIL_CHARS:]


def _close_run(run_id: int, log: str, unclosed_reason: str) -> None:
    """Keep the log tail; a row the recorder left running is failed with `unclosed_reason`."""
    ScrapeRun.objects.filter(pk=run_id).update(log_tail=log_tail(log))
    closed = ScrapeRun.objects.filter(pk=run_id, status=ScrapeRun.Status.RUNNING).update(
        status=ScrapeRun.Status.FAILED,
        finish_reason=unclosed_reason,
        finished_at=timezone.now(),
    )
    if closed:
        logger.error("Run %d never closed its row; marked failed (%s)", run_id, unclosed_reason)


@shared_task
def notify_scrape_problems() -> dict:
    """E-mail the principal about blocked, needs-human, failed or twice-empty scans.

    At most once per source per 24 h; see apps/listings/notify.py. Scheduled hourly."""
    from apps.listings.notify import send_problem_notifications

    return send_problem_notifications()


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
                # The markers decide with JEV_MODE off; Jev reads the page in shadow and on.
                if ingest.page_expired(pk, resp.text, STALE_MARKERS):
                    Listing.objects.filter(pk=pk).update(is_active=False)
                    deactivated += 1
        except requests.RequestException:
            pass  # network error — skip, will retry next run

        # Be polite: 1s delay between requests
        time.sleep(1)

    logger.info("Validity check: %d/%d deactivated", deactivated, len(listings))
    return {"checked": len(listings), "deactivated": deactivated}
