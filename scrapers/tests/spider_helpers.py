"""Helpers the fixture-based spider tests share (no network, no database)."""

import asyncio
import json
from pathlib import Path

from scrapy import Request
from scrapy.http import HtmlResponse
from scrapy.utils.test import get_crawler

from realestate_spiders.pipelines import NormalizePipeline, ScrubPipeline, ValidatePipeline

FIXTURES = Path(__file__).parent / "fixtures"

# What the redacted fixtures carry in place of real contact data; none may reach an item.
PLACEHOLDERS = ("+370 600 00000", "agent@example.invalid", "Agentas Pavardenis", "Pavardenį")


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_response(url: str, markup: str, **meta) -> HtmlResponse:
    request = Request(url, meta=meta)
    return HtmlResponse(url, body=markup.encode("utf-8"), encoding="utf-8", request=request)


def make_spider(spider_cls, **kwargs):
    return get_crawler(spider_cls)._create_spider(**kwargs)


def collect_start(spider) -> list[Request]:
    async def run():
        return [r async for r in spider.start()]

    return asyncio.run(run())


def split_requests(spider, requests):
    """(detail requests, next-page requests) from what `parse_index` yielded."""
    details = [r for r in requests if r.callback == spider.parse_detail]
    pages = [r for r in requests if r.callback == spider.parse_index]
    return details, pages


def dumped(item) -> str:
    return json.dumps(dict(item), ensure_ascii=False, default=str)


def run_pipelines(spider, item):
    for pipeline in (ScrubPipeline(), ValidatePipeline(), NormalizePipeline()):
        item = pipeline.process_item(item, spider)
    return item
