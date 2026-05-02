"""Scrapy middleware stubs for realestate_spiders.

These are placeholders for custom middleware logic. Enable them in settings.py
when needed.
"""

from __future__ import annotations

from typing import Any

from scrapy import Request, Spider, signals
from scrapy.crawler import Crawler
from scrapy.http import Response


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
