"""run_spider: the one Celery task that runs every registry source.

Spider tasks must run on the scraper worker (scrapers image), not the backend worker.
The backend image has no Scrapy, so they are routed to a dedicated `scraping` queue
consumed by `scraper_worker`, where the scrapers project is mounted at /app.
"""

import dataclasses
import importlib
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings as django_settings

from apps.listings.models import ScrapeRun
from apps.listings.sources import get_source
from apps.listings.tasks import LOG_TAIL_CHARS, LOG_TAIL_LINES, run_spider
from config.celery import app as celery_app

# run_spider writes the ScrapeRun row itself (#8), so every crawl test needs the database.
pytestmark = pytest.mark.django_db

PHONE = "+370 600 00001"  # fake

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
        run_spider("ntseptynetas")
    run.assert_not_called()


def test_login_walled_spider_is_refused() -> None:
    with patch("subprocess.run") as run, pytest.raises(ValueError, match="not built"):
        run_spider("rasknt")
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


# --- ScrapeRun v2 (#8): run_spider owns the row, so beat runs keep their log -------------


def _crawl_cmd(run: MagicMock) -> list[str]:
    return run.call_args.args[0]


class TestRunRow:
    def test_creates_the_row_and_hands_its_id_to_the_crawl(self) -> None:
        with patch("subprocess.run", return_value=_done()) as run:
            result = run_spider("domoplius")
        row = ScrapeRun.objects.get()
        assert row.spider == "domoplius"
        assert row.trigger == ScrapeRun.Trigger.MANUAL
        assert row.args == {
            "property_type": "namai",
            "region": "kretinga",
            "max_pages": "10",
            "target": "house",
        }
        assert row.start_url.startswith("https://domoplius.lt/")
        assert _crawl_cmd(run)[-2:] == ["-s", f"NT_SCRAPE_RUN_ID={row.pk}"]
        assert result["crawls"][0]["run_id"] == row.pk

    def test_beat_trigger_is_recorded_and_not_sent_to_the_spider(self) -> None:
        with patch("subprocess.run", return_value=_done()) as run:
            run_spider("domoplius", trigger="beat")
        assert ScrapeRun.objects.get().trigger == ScrapeRun.Trigger.BEAT
        assert not any(pair.startswith("trigger=") for pair in _a_args(_crawl_cmd(run)))

    def test_unknown_trigger_fails(self) -> None:
        with patch("subprocess.run") as run, pytest.raises(ValueError, match="cron"):
            run_spider("domoplius", trigger="cron")
        run.assert_not_called()
        assert not ScrapeRun.objects.exists()

    def test_one_row_per_target_crawl(self, settings) -> None:
        settings.SCAN_PROPERTY_TYPES = ["house"]
        with patch("subprocess.run", return_value=_done()):
            run_spider("domoplius")
        assert ScrapeRun.objects.count() == 1

    def test_log_tail_is_kept_scrubbed_and_capped(self) -> None:
        lines = [f"2026-11-15 INFO: line {i}" for i in range(1000)]
        lines.append(f"WARNING: Dropped item, contact {PHONE} or info@example.test")
        done = MagicMock(returncode=0, stdout="", stderr="\n".join(lines))
        with patch("subprocess.run", return_value=done):
            run_spider("domoplius")
        tail = ScrapeRun.objects.get().log_tail
        assert tail.splitlines()[-1].startswith("WARNING: Dropped item")
        assert "line 999" in tail
        assert "line 0\n" not in tail
        assert len(tail.splitlines()) <= LOG_TAIL_LINES
        assert len(tail) <= LOG_TAIL_CHARS
        assert PHONE not in tail
        assert "info@example.test" not in tail

    def test_one_huge_line_is_capped_too(self) -> None:
        done = MagicMock(returncode=0, stdout="", stderr="x" * (LOG_TAIL_CHARS * 3))
        with patch("subprocess.run", return_value=done):
            run_spider("domoplius")
        assert len(ScrapeRun.objects.get().log_tail) <= LOG_TAIL_CHARS

    def test_row_closed_by_the_recorder_keeps_its_status(self) -> None:
        def crawl(cmd: list[str], **kwargs: object) -> MagicMock:
            # What ScrapeRunRecorder does inside the crawl process.
            ScrapeRun.objects.update(status=ScrapeRun.Status.OK, finish_reason="finished")
            return MagicMock(returncode=0, stdout="", stderr="INFO: Spider closed (finished)")

        with patch("subprocess.run", side_effect=crawl):
            run_spider("domoplius")
        row = ScrapeRun.objects.get()
        assert row.status == ScrapeRun.Status.OK
        assert row.finish_reason == "finished"
        assert row.log_tail == "INFO: Spider closed (finished)"

    def test_crash_before_the_recorder_closed_the_row_is_failed(self) -> None:
        failed = MagicMock(returncode=1, stdout="", stderr="Traceback ... KeyError: 'boom'")
        with patch("subprocess.run", return_value=failed):
            run_spider("domoplius")
        row = ScrapeRun.objects.get()
        assert row.status == ScrapeRun.Status.FAILED
        assert row.finish_reason == "exit 1"
        assert row.finished_at is not None
        assert "KeyError" in row.log_tail

    def test_clean_exit_that_never_closed_the_row_is_failed_unrecorded(self) -> None:
        with patch("subprocess.run", return_value=_done()):
            run_spider("domoplius")
        row = ScrapeRun.objects.get()
        assert row.status == ScrapeRun.Status.FAILED
        assert row.finish_reason == "unrecorded"

    def test_timeout_is_failed_now_with_the_partial_log(self) -> None:
        timeout = subprocess.TimeoutExpired(
            cmd=["scrapy"], timeout=3600, output=b"", stderr=b"INFO: Crawled 3 pages"
        )
        with patch("subprocess.run", side_effect=timeout):
            run_spider("domoplius")
        row = ScrapeRun.objects.get()
        assert row.status == ScrapeRun.Status.FAILED
        assert row.finish_reason == "timeout"
        assert row.finished_at is not None
        assert row.log_tail == "INFO: Crawled 3 pages"

    def test_crawl_that_cannot_start_is_failed_and_raised(self) -> None:
        with (
            patch("subprocess.run", side_effect=FileNotFoundError("scrapy")),
            pytest.raises(FileNotFoundError),
        ):
            run_spider("domoplius")
        row = ScrapeRun.objects.get()
        assert row.status == ScrapeRun.Status.FAILED
        assert row.finish_reason == "error: FileNotFoundError"
        assert row.finished_at is not None

    def test_long_start_url_is_cut_to_the_column(self, settings) -> None:
        settings.SCAN_PROPERTY_TYPES = ["house"]
        source = get_source("domoplius")
        long_url = "https://domoplius.lt/skelbimai?q=" + "a" * 1000
        target = dataclasses.replace(source.target("house"), start_urls=(long_url,))
        with (
            patch(
                "apps.listings.tasks.get_source",
                return_value=dataclasses.replace(source, targets=(target,)),
            ),
            patch("subprocess.run", return_value=_done()),
        ):
            run_spider("domoplius")
        assert ScrapeRun.objects.get().start_url == long_url[: ScrapeRun.START_URL_MAX_LENGTH]
