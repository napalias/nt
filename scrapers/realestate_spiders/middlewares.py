"""Scrapy middlewares for realestate_spiders.

BlockDetectionMiddleware is enabled in settings.py. The two stubs below it are placeholders
for custom logic; enable them there when needed.
"""

from __future__ import annotations

import logging
from typing import Any

import django_setup

# The wall question reads settings.JEV_MODE, so Django settings must be configured.
django_setup.setup()

from apps.classifier import ingest  # noqa: E402
from apps.listings.blocks import BlockKind, classify_response, stat_key  # noqa: E402
from scrapy import Request, Spider, signals  # noqa: E402
from scrapy.crawler import Crawler  # noqa: E402
from scrapy.exceptions import IgnoreRequest  # noqa: E402
from scrapy.http import Response, TextResponse  # noqa: E402
from scrapy.statscollectors import StatsCollector  # noqa: E402

logger = logging.getLogger(__name__)

# Most short 200 pages one crawl puts to Jev as a wall question (JEV_MODE shadow or on).
MAX_WALL_CHECKS = 30

ROBOTS_MESSAGE = "robots.txt"  # RobotsTxtMiddleware: IgnoreRequest("Forbidden by robots.txt")


class BlockDetectionMiddleware:
    """Count block pages, 401/403/429 answers and robots-forbidden start URLs in the stats.

    Writes `nt/block/<kind>` (apps/listings/blocks.py); ScrapeRunRecorder turns them into
    ScrapeRun.block_kind, needs_human and the blocked/partial status. Responses pass on
    unchanged: spiders find nothing in a block page, and the run says why.
    """

    def __init__(self, stats: StatsCollector) -> None:
        self.stats = stats
        self.warned: set[BlockKind] = set()
        self.wall_checks = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> BlockDetectionMiddleware:
        return cls(crawler.stats)

    def _judge_wall(self, response: Response, text: str) -> BlockKind | None:
        """Ask Jev about a short 200 page the code markers did not recognise (#18).

        Only with JEV_MODE shadow or on, only for pages with almost nothing to read, and at
        most MAX_WALL_CHECKS a crawl. Shadow logs Jev's answer beside the code's ("no wall");
        only `on` returns a kind. The call blocks the reactor for up to the Jev timeout, which
        is why it is rationed.
        """
        if not ingest.screens_active() or not ingest.looks_ambiguous(text):
            return None
        if self.wall_checks >= MAX_WALL_CHECKS:
            return None
        self.wall_checks += 1
        self.stats.inc_value("nt/wall_judged")
        return ingest.judge_wall(response.url, text)

    def _record(self, kind: BlockKind, url: str) -> None:
        self.stats.inc_value(stat_key(kind))
        if kind not in self.warned:  # once per kind, not once per page
            self.warned.add(kind)
            logger.warning("Blocked (%s): %s", kind.value, url)

    def process_response(self, request: Request, response: Response) -> Response:
        text = response.text if isinstance(response, TextResponse) else None
        redirected = bool(request.meta.get("redirect_urls"))
        kind = classify_response(response.status, response.url, text, redirected)
        if kind is None and response.status == 200 and text is not None:
            kind = self._judge_wall(response, text)
        if kind is not None:
            self._record(kind, response.url)
        return response

    def process_exception(self, request: Request, exception: Exception) -> None:
        # RobotsTxtMiddleware raises from process_request; every middleware's
        # process_exception sees it. Only start URLs count: a forbidden detail page is one
        # lost item (stat robotstxt/forbidden), a forbidden start URL is a dead crawl.
        is_robots = isinstance(exception, IgnoreRequest) and ROBOTS_MESSAGE in str(exception)
        if is_robots and request.meta.get("is_start_request"):
            self._record(BlockKind.ROBOTS, request.url)
        return None


class RealEstateSpiderMiddleware:
    """Spider middleware for realestate_spiders."""

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> RealEstateSpiderMiddleware:
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_spider_input(self, response: Response, spider: Spider) -> None:
        return None

    def process_spider_output(self, response: Response, result: Any, spider: Spider) -> Any:
        yield from result

    def process_spider_exception(
        self, response: Response, exception: Exception, spider: Spider
    ) -> None:
        pass

    def process_start_requests(self, start_requests: Any, spider: Spider) -> Any:
        yield from start_requests

    def spider_opened(self, spider: Spider) -> None:
        spider.logger.info("Spider opened: %s", spider.name)


class RealEstateDownloaderMiddleware:
    """Downloader middleware for realestate_spiders."""

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> RealEstateDownloaderMiddleware:
        s = cls()
        crawler.signals.connect(s.spider_opened, signal=signals.spider_opened)
        return s

    def process_request(self, request: Request, spider: Spider) -> None:
        return None

    def process_response(self, request: Request, response: Response, spider: Spider) -> Response:
        return response

    def process_exception(self, request: Request, exception: Exception, spider: Spider) -> None:
        pass

    def spider_opened(self, spider: Spider) -> None:
        spider.logger.info("Spider opened: %s", spider.name)
