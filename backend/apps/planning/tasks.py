import logging
import subprocess

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def scrape_tpdris(max_pages: int = 0) -> dict:
    """Run the TPDRIS spider to scrape territorial planning documents.

    Designed to be triggered from celery-beat (weekly schedule).
    """
    cmd = ["scrapy", "crawl", "tpdris"]
    if max_pages:
        cmd.extend(["-a", f"max_pages={max_pages}"])

    logger.info("Starting TPDRIS spider: %s", " ".join(cmd))

    result = subprocess.run(
        cmd,
        cwd="/app",
        capture_output=True,
        text=True,
        timeout=7200,
    )

    if result.returncode != 0:
        logger.error(
            "TPDRIS spider failed (exit %d): %s",
            result.returncode,
            result.stderr[-500:],
        )
    else:
        logger.info("TPDRIS spider completed successfully")

    return {
        "spider": "tpdris",
        "exit_code": result.returncode,
        "stdout_tail": result.stdout[-200:] if result.stdout else "",
        "stderr_tail": result.stderr[-200:] if result.stderr else "",
    }
