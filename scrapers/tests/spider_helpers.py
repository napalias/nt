"""Helpers the listing-spider tests share: saved pages, fake responses, a spider with a crawler."""

import asyncio
from pathlib import Path

from scrapy import Request
from scrapy.http import HtmlResponse
from scrapy.utils.test import get_crawler

FIXTURES = Path(__file__).parent / "fixtures"
SPIDERS = Path(__file__).parent.parent / "realestate_spiders" / "spiders" / "listings"
# Contact values the fixtures carry in place of the real ones; none may reach an item.
PLACEHOLDER_PHONE = "+370 600 00000"
PLACEHOLDER_EMAIL = "agent@example.invalid"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_response(url: str, markup: str, **meta) -> HtmlResponse:
    request = Request(url, meta=meta)
    return HtmlResponse(url, body=markup.encode("utf-8"), encoding="utf-8", request=request)


def make_spider(spider_class, **kwargs):
    return get_crawler(spider_class)._create_spider(**kwargs)


def collect_start(spider) -> list[Request]:
    async def run():
        return [r async for r in spider.start()]

    return asyncio.run(run())


def details_of(spider, response) -> list:
    """The items one listing page yields."""
    return list(spider.parse_detail(response))
