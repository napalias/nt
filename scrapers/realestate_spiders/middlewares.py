"""Scrapy middlewares for realestate_spiders.

BlockDetectionMiddleware is enabled in settings.py. The two stubs below it are placeholders
for custom logic; enable them there when needed.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from apps.listings.blocks import STATUS_KINDS, BlockKind, stat_key
from scrapy import Request, Spider, signals
from scrapy.crawler import Crawler
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Response, TextResponse
from scrapy.statscollectors import StatsCollector

logger = logging.getLogger(__name__)

# Challenge pages are small; a real page's markers sit near the top. Bound the work.
MAX_SCAN_CHARS = 300_000

# Each marker appears only on the block page itself, never on a normal page of the site.
# Deliberately absent: "g-recaptcha" (contact forms), "_pxAppId" and Cloudflare's
# "/cdn-cgi/challenge-platform/" script (both injected into every normal page).
CAPTCHA_MARKERS = (
    "px-captcha",  # PerimeterX (aruodas)
    "access to this page has been denied",  # PerimeterX block page title
    "captcha-delivery.com",  # DataDome
)
CHALLENGE_MARKERS = (
    "_cf_chl_opt",  # Cloudflare challenge bootstrap
    "cf-browser-verification",  # Cloudflare, older challenge page
    "checking your browser before accessing",
    "incapsula incident id",  # Imperva block page
)
CHALLENGE_TITLES = ("just a moment", "attention required! | cloudflare")
CAPTCHA_TITLES = ("captcha", "are you a robot", "are you human", "nesate robotas")

# "Prisijungimas" / "prisijungti" = log in (Lithuanian).
LOGIN_TITLE_RE = re.compile(r"prisijung|\blog ?in\b|\bsign ?in\b", re.IGNORECASE)
PASSWORD_INPUT_RE = re.compile(r"<input[^>]+type\s*=\s*[\"']?password", re.IGNORECASE)
LOGIN_PATH_RE = re.compile(
    r"(?:^|/)(?:login|log-in|signin|sign-in|prisijung[a-z]*)(?:[/?#.]|$)", re.IGNORECASE
)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

ROBOTS_MESSAGE = "robots.txt"  # RobotsTxtMiddleware: IgnoreRequest("Forbidden by robots.txt")


def _path_and_query(url: str) -> str:
    rest = url.split("://", 1)[-1]
    slash = rest.find("/")
    return rest[slash:] if slash >= 0 else "/"


def classify_response(
    status: int, url: str, text: str | None, redirected: bool = False
) -> BlockKind | None:
    """Why this response is not the page we asked for, or None if it is.

    Body markers win over the status code, so a PerimeterX page counts as a captcha whether
    it came with 200 or 403. A login page is one titled "log in" that has a password field
    (a header login modal on a normal page has a normal title), or one the site redirected
    us to. `text` is None for non-text responses: only the status speaks then.
    """
    body = (text or "")[:MAX_SCAN_CHARS].lower()
    title_match = TITLE_RE.search(body)
    title = title_match.group(1).strip() if title_match else ""

    if any(m in body for m in CAPTCHA_MARKERS) or any(t in title for t in CAPTCHA_TITLES):
        return BlockKind.CAPTCHA
    if any(m in body for m in CHALLENGE_MARKERS) or title.startswith(CHALLENGE_TITLES):
        return BlockKind.CHALLENGE
    if LOGIN_TITLE_RE.search(title) and PASSWORD_INPUT_RE.search(body):
        return BlockKind.LOGIN
    if redirected and LOGIN_PATH_RE.search(_path_and_query(url)):
        return BlockKind.LOGIN
    return STATUS_KINDS.get(status)


class BlockDetectionMiddleware:
    """Count block pages, 401/403/429 answers and robots-forbidden start URLs in the stats.

    Writes `nt/block/<kind>` (apps/listings/blocks.py); ScrapeRunRecorder turns them into
    ScrapeRun.block_kind, needs_human and the blocked/partial status. Responses pass on
    unchanged: spiders find nothing in a block page, and the run says why.
    """

    def __init__(self, stats: StatsCollector) -> None:
        self.stats = stats
        self.warned: set[BlockKind] = set()

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> BlockDetectionMiddleware:
        return cls(crawler.stats)

    def _record(self, kind: BlockKind, url: str) -> None:
        self.stats.inc_value(stat_key(kind))
        if kind not in self.warned:  # once per kind, not once per page
            self.warned.add(kind)
            logger.warning("Blocked (%s): %s", kind.value, url)

    def process_response(self, request: Request, response: Response) -> Response:
        text = response.text if isinstance(response, TextResponse) else None
        redirected = bool(request.meta.get("redirect_urls"))
        kind = classify_response(response.status, response.url, text, redirected)
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
