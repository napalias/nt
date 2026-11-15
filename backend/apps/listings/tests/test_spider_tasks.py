"""run_spider: the one Celery task that runs every registry source.

Spider tasks must run on the scraper worker (scrapers image), not the backend worker.
The backend image has no Scrapy, so they are routed to a dedicated `scraping` queue
consumed by `scraper_worker`, where the scrapers project is mounted at /app.
"""

import importlib
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings as django_settings

from apps.listings.tasks import run_spider
from config.celery import app as celery_app

LEGACY_TASKS = (
    "apps.permits.tasks.run_infostatyba_spider",
    "apps.planning.tasks.scrape_tpdris",
)


def _done() -> MagicMock:
    return MagicMock(returncode=0, stdout="ok", stderr="")


def _a_args(cmd: list[str]) -> list[str]:
    return [cmd[i + 1] for i, part in enumerate(cmd) if part == "-a"]


def test_run_spider_routes_to_scraping_queue() -> None:
    route = celery_app.amqp.router.route({}, "apps.listings.tasks.run_spider")
    assert route["queue"].name == "scraping"


def test_non_spider_tasks_stay_on_default_queue() -> None:
    route = celery_app.amqp.router.route({}, "apps.classifier.tasks.classify_new_listings")
    assert route["queue"].name == "celery"


@pytest.mark.parametrize("task_name", LEGACY_TASKS)
def test_gov_spider_tasks_are_folded_into_run_spider(task_name: str) -> None:
    module, _, _ = task_name.rpartition(".")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)
    assert task_name not in django_settings.CELERY_TASK_ROUTES


@pytest.mark.parametrize("spider", ["domoplius", "infostatyba", "tpdris"])
def test_runs_scrapy_from_scrapers_dir(spider: str) -> None:
    with patch("subprocess.run", return_value=_done()) as run:
        result = run_spider(spider)
    args, kwargs = run.call_args
    assert args[0][:3] == ["scrapy", "crawl", spider]
    assert kwargs["cwd"] == "/app"
    assert result["spider"] == spider
    assert result["crawls"][0]["exit_code"] == 0


def test_forwards_registry_args_and_delay() -> None:
    with patch("subprocess.run", return_value=_done()) as run:
        run_spider("domoplius")
    cmd = run.call_args.args[0]
    assert ["-s", "DOWNLOAD_DELAY=3"] == cmd[3:5]
    assert set(_a_args(cmd)) == {
        "property_type=namai",
        "region=kretinga",
        "max_pages=10",
        "target=house",
    }


def test_call_kwargs_override_registry_args() -> None:
    with patch("subprocess.run", return_value=_done()) as run:
        run_spider("domoplius", max_pages=1, extra="yes")
    pairs = _a_args(run.call_args.args[0])
    assert "max_pages=1" in pairs
    assert "max_pages=10" not in pairs
    assert "extra=yes" in pairs


def test_gov_source_uses_its_own_timeout() -> None:
    with patch("subprocess.run", return_value=_done()) as run:
        run_spider("tpdris")
    assert run.call_args.kwargs["timeout"] == 7200
    assert _a_args(run.call_args.args[0]) == ["max_pages=50"]


def test_switched_off_property_type_runs_nothing(settings) -> None:
    settings.SCAN_PROPERTY_TYPES = ["flat"]
    with patch("subprocess.run", return_value=_done()) as run:
        result = run_spider("domoplius")
    run.assert_not_called()
    assert result["crawls"] == []


def test_unknown_configured_property_type_fails_loudly(settings) -> None:
    settings.SCAN_PROPERTY_TYPES = ["villa"]
    with patch("subprocess.run") as run, pytest.raises(ValueError, match="villa"):
        run_spider("domoplius")
    run.assert_not_called()


def test_explicit_property_type_without_a_target_fails() -> None:
    with patch("subprocess.run") as run, pytest.raises(ValueError, match="flat"):
        run_spider("domoplius", property_type="flat")
    run.assert_not_called()


def test_unknown_spider_fails() -> None:
    with patch("subprocess.run") as run, pytest.raises(ValueError, match="nope"):
        run_spider("nope")
    run.assert_not_called()


def test_unbuilt_spider_is_refused() -> None:
    with patch("subprocess.run") as run, pytest.raises(ValueError, match="not built"):
        run_spider("realu")
    run.assert_not_called()


def test_paused_source_can_still_be_run_by_hand() -> None:
    with patch("subprocess.run", return_value=_done()) as run:
        run_spider("aruodas")
    assert "max_pages=3" in _a_args(run.call_args.args[0])


def test_failed_crawl_is_reported() -> None:
    failed = MagicMock(returncode=1, stdout="", stderr="Traceback ... boom")
    with patch("subprocess.run", return_value=failed):
        result = run_spider("domoplius")
    assert result["crawls"][0]["exit_code"] == 1
    assert "boom" in result["crawls"][0]["stderr_tail"]


def test_timed_out_crawl_is_reported_not_raised() -> None:
    timeout = subprocess.TimeoutExpired(cmd=["scrapy"], timeout=3600)
    with patch("subprocess.run", side_effect=timeout):
        result = run_spider("domoplius")
    assert result["crawls"][0]["timed_out"] is True
    assert result["crawls"][0]["exit_code"] is None
