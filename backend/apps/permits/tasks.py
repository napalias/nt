from __future__ import annotations

import logging
import subprocess

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def run_infostatyba_spider(self, max_pages: int = 0) -> str:
    """Run the infostatyba Scrapy spider as a subprocess.

    Args:
        max_pages: Limit number of search result pages (0 = unlimited).

    Returns:
        Spider stdout/stderr summary.
    """
    cmd = ["scrapy", "crawl", "infostatyba"]
    if max_pages:
        cmd += ["-a", f"max_pages={max_pages}"]

    logger.info("Starting infostatyba spider: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            cwd="/scrapers",
            capture_output=True,
            text=True,
            timeout=7200,  # 2 hours max
        )
        if result.returncode != 0:
            logger.error("Infostatyba spider failed: %s", result.stderr[-500:])
            raise self.retry(exc=RuntimeError(result.stderr[-200:]))
        logger.info("Infostatyba spider completed successfully")
        return result.stdout[-500:]
    except subprocess.TimeoutExpired:
        logger.error("Infostatyba spider timed out after 2 hours")
        return "Spider timed out"
