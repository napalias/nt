"""Spider tasks must run on the scraper worker (scrapers image), not the backend worker.

The backend image has no Scrapy, so spider tasks are routed to a dedicated `scraping`
queue consumed by `scraper_worker`, where the scrapers project is mounted at /app.
"""

from unittest.mock import MagicMock, patch

import pytest

from apps.listings.tasks import run_spider
from apps.permits.tasks import run_infostatyba_spider
from apps.planning.tasks import scrape_tpdris
from config.celery import app as celery_app

SPIDER_TASKS = [
    "apps.listings.tasks.run_spider",
    "apps.permits.tasks.run_infostatyba_spider",
    "apps.planning.tasks.scrape_tpdris",
]


@pytest.mark.parametrize("task_name", SPIDER_TASKS)
def test_spider_tasks_route_to_scraping_queue(task_name: str) -> None:
    route = celery_app.amqp.router.route({}, task_name)
    assert route["queue"].name == "scraping"


def test_non_spider_tasks_stay_on_default_queue() -> None:
    route = celery_app.amqp.router.route({}, "apps.classifier.tasks.classify_new_listings")
    assert route["queue"].name == "celery"


@pytest.mark.parametrize(
    ("call", "spider"),
    [
        (lambda: run_spider("domoplius", max_pages=1), "domoplius"),
        (lambda: run_infostatyba_spider(max_pages=1), "infostatyba"),
        (lambda: scrape_tpdris(max_pages=1), "tpdris"),
    ],
)
def test_spider_tasks_run_scrapy_from_scrapers_dir(call, spider: str) -> None:
    done = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("subprocess.run", return_value=done) as run:
        call()
    args, kwargs = run.call_args
    assert args[0][:3] == ["scrapy", "crawl", spider]
    assert kwargs["cwd"] == "/app"
