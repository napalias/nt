"""ScrapeRunRecorder (#8): fills in the row run_spider made, records the start URL and the
command-line args, and writes block kind, needs-human and status from the crawl stats.

The database is never touched: ScrapeRun.objects is mocked.

    docker compose exec scraper_worker python -m unittest discover -s tests -v
"""

import unittest
import warnings
from unittest.mock import MagicMock, patch

import django_setup

django_setup.setup()  # before any model import

from apps.listings.blocks import BlockKind, stat_key  # noqa: E402
from apps.listings.models import ScrapeRun  # noqa: E402
from scrapy import Request, Spider, signals  # noqa: E402
from scrapy.crawler import Crawler  # noqa: E402
from scrapy.exceptions import DropItem  # noqa: E402
from scrapy.http import HtmlResponse  # noqa: E402
from scrapy.utils.test import get_crawler  # noqa: E402
from twisted.python.failure import Failure  # noqa: E402

from realestate_spiders.extensions import ScrapeRunRecorder, crawl_args  # noqa: E402

ARGV = ["/usr/local/bin/scrapy", "crawl", "domoplius", "-a", "region=kretinga", "-a", "x=1"]


def _recorder(settings: dict | None = None) -> tuple[ScrapeRunRecorder, Spider]:
    crawler = get_crawler(Spider, settings_dict=settings or {})
    crawler.stats.open_spider()
    return ScrapeRunRecorder.from_crawler(crawler), Spider(name="domoplius")


class SpiderOpenedTest(unittest.TestCase):
    def test_fills_in_the_row_run_spider_made(self) -> None:
        recorder, spider = _recorder({"NT_SCRAPE_RUN_ID": 7})
        row = MagicMock(pk=7)
        with patch.object(ScrapeRun, "objects") as objects:
            objects.filter.return_value.first.return_value = row
            recorder.spider_opened(spider)
        objects.filter.assert_any_call(pk=7, spider="domoplius")
        objects.create.assert_not_called()
        self.assertIs(recorder.run, row)

    def test_hand_started_crawl_gets_a_manual_row_with_its_command_line_args(self) -> None:
        recorder, spider = _recorder()
        with patch.object(ScrapeRun, "objects") as objects, patch("sys.argv", ARGV):
            recorder.spider_opened(spider)
        kwargs = objects.create.call_args.kwargs
        self.assertEqual(kwargs["trigger"], ScrapeRun.Trigger.MANUAL)
        self.assertEqual(kwargs["args"], {"region": "kretinga", "x": "1"})

    def test_unknown_run_id_falls_back_to_a_new_row(self) -> None:
        recorder, spider = _recorder({"NT_SCRAPE_RUN_ID": 99})
        with (
            patch.object(ScrapeRun, "objects") as objects,
            self.assertLogs("realestate_spiders.extensions", "WARNING"),
        ):
            objects.filter.return_value.first.return_value = None
            recorder.spider_opened(spider)
        objects.create.assert_called_once()

    def test_database_error_never_breaks_the_crawl(self) -> None:
        recorder, spider = _recorder()
        with (
            patch.object(ScrapeRun, "objects") as objects,
            self.assertLogs("realestate_spiders.extensions", "WARNING"),
        ):
            objects.filter.side_effect = RuntimeError("db down")
            recorder.spider_opened(spider)
        self.assertIsNone(recorder.run)


def _send(crawler: Crawler, signal: object, **kwargs: object) -> None:
    """Fire a signal the way Scrapy's engine does (keyword args through the dispatcher),
    and fail on an error in a ScrapeRunRecorder handler, which send_catch_log would
    otherwise only log. Scrapy's own extensions on the test crawler (CoreStats, which
    wants a real engine) are not under test here."""
    for receiver, result in crawler.signals.send_catch_log(signal, **kwargs):
        if isinstance(getattr(receiver, "__self__", None), ScrapeRunRecorder) and isinstance(
            result, Failure
        ):
            raise AssertionError(f"{receiver} failed: {result.getTraceback()}")


def _wired(settings: dict | None = None) -> tuple[Crawler, ScrapeRunRecorder, Spider]:
    crawler = get_crawler(Spider, settings_dict=settings or {})
    crawler.stats.open_spider()
    return crawler, ScrapeRunRecorder.from_crawler(crawler), Spider(name="domoplius")


def _start_request(url: str) -> Request:
    return Request(url, meta={"is_start_request": True})


class SignalWiringTest(unittest.TestCase):
    """The handlers through Scrapy's own dispatch, with the arguments each signal sends
    (scrapy/core/engine.py, scraper.py in Scrapy 2.20)."""

    def test_every_handler_matches_its_signal(self) -> None:
        crawler = get_crawler(Spider)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ScrapeRunRecorder.from_crawler(crawler)
        self.assertEqual([str(w.message) for w in caught if "does not send" in str(w.message)], [])

    def test_a_whole_crawl_reaches_the_row(self) -> None:
        crawler, recorder, spider = _wired({"NT_SCRAPE_RUN_ID": 4})
        row = MagicMock(pk=4)
        response = HtmlResponse("https://x.lt/start", body=b"<html></html>")
        with patch.object(ScrapeRun, "objects") as objects:
            objects.filter.return_value.first.return_value = row
            _send(crawler, signals.spider_opened, spider=spider)
            _send(
                crawler,
                signals.request_scheduled,
                request=_start_request("https://x.lt/start"),
                spider=spider,
            )
            _send(crawler, signals.item_scraped, item={}, response=response, spider=spider)
            _send(
                crawler,
                signals.item_dropped,
                item={},
                response=response,
                spider=spider,
                exception=DropItem("Cannot geocode listing for: Vilniaus g. 1"),
            )
            crawler.stats.set_value("item_scraped_count", 1)
            crawler.stats.set_value("item_dropped_count", 1)
            _send(crawler, signals.spider_closed, spider=spider, reason="finished", error=None)
        self.assertIs(recorder.run, row)
        updates = [c.kwargs for c in objects.filter.return_value.update.call_args_list]
        self.assertIn({"start_url": "https://x.lt/start"}, updates)
        closed = updates[-1]
        self.assertEqual(closed["status"], ScrapeRun.Status.PARTIAL)  # 50% dropped
        self.assertEqual(closed["finish_reason"], "finished")
        self.assertEqual(closed["drop_reasons"], {"Cannot geocode listing for": 1})
        self.assertEqual(recorder.seen, 2)


class StartUrlTest(unittest.TestCase):
    def test_first_start_request_is_the_start_url(self) -> None:
        crawler, recorder, spider = _wired()
        recorder.run = MagicMock(pk=3)
        with patch.object(ScrapeRun, "objects") as objects:
            for request in (
                Request("https://x.lt/detail/1"),
                _start_request("https://x.lt/start?page=1"),
                _start_request("https://x.lt/start?page=2"),
            ):
                _send(crawler, signals.request_scheduled, request=request, spider=spider)
        objects.filter.assert_called_once_with(pk=3)
        objects.filter.return_value.update.assert_called_once_with(
            start_url="https://x.lt/start?page=1"
        )

    def test_long_start_url_is_cut_to_the_column(self) -> None:
        crawler, recorder, spider = _wired()
        recorder.run = MagicMock(pk=3)
        url = "https://x.lt/start?q=" + "a" * 1000
        with patch.object(ScrapeRun, "objects") as objects:
            _send(crawler, signals.request_scheduled, request=_start_request(url), spider=spider)
        stored = objects.filter.return_value.update.call_args.kwargs["start_url"]
        self.assertEqual(len(stored), ScrapeRun.START_URL_MAX_LENGTH)
        self.assertEqual(stored, url[:500])

    def test_database_error_is_logged_not_raised(self) -> None:
        crawler, recorder, spider = _wired()
        recorder.run = MagicMock(pk=3)
        with (
            patch.object(ScrapeRun, "objects") as objects,
            self.assertLogs("realestate_spiders.extensions", "WARNING"),
        ):
            objects.filter.side_effect = RuntimeError("db down")
            _send(
                crawler,
                signals.request_scheduled,
                request=_start_request("https://x.lt/start"),
                spider=spider,
            )


class CrawlArgsTest(unittest.TestCase):
    def test_outside_a_crawl_command_uses_spider_attributes(self) -> None:
        spider = Spider(name="domoplius", max_pages=2, region="kretinga")
        self.assertEqual(
            crawl_args(spider, ["python", "-m", "unittest"]),
            {"max_pages": "2", "region": "kretinga"},
        )


class SpiderClosedTest(unittest.TestCase):
    def _close(self, reason: str, **stats: int) -> dict:
        crawler, recorder, spider = _wired()
        with patch.object(ScrapeRun, "objects") as objects:
            # Opened first, as in a real crawl (CoreStats needs its start time to close).
            _send(crawler, signals.spider_opened, spider=spider)
            for key, value in stats.items():
                crawler.stats.set_value(key, value)
            _send(crawler, signals.spider_closed, spider=spider, reason=reason, error=None)
        self.assertIs(recorder.run, objects.create.return_value)
        return objects.filter.return_value.update.call_args.kwargs

    def test_block_stats_reach_the_row(self) -> None:
        fields = self._close(
            "finished", **{stat_key(BlockKind.CAPTCHA): 1, "scheduler/enqueued": 1}
        )
        self.assertEqual(fields["status"], ScrapeRun.Status.BLOCKED)
        self.assertEqual(fields["block_kind"], "captcha")
        self.assertIs(fields["needs_human"], True)
        self.assertEqual(fields["links_seen"], 1)
        self.assertIn("finished_at", fields)

    def test_robots_denied_close_is_blocked_not_failed(self) -> None:
        # Scrapy closes the crawl with this reason when robots.txt forbade every start URL.
        fields = self._close("robotstxt_denied", **{"robotstxt/forbidden": 1})
        self.assertEqual(fields["status"], ScrapeRun.Status.BLOCKED)
        self.assertEqual(fields["block_kind"], "robots")
        self.assertEqual(fields["finish_reason"], "robotstxt_denied")


if __name__ == "__main__":
    unittest.main()
