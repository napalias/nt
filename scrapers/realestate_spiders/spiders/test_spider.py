"""No-op test spider that yields one fake item to verify the pipeline runs.

Usage:
    scrapy crawl test
"""

from datetime import UTC, datetime

import scrapy
from scrapy.http import Response

from realestate_spiders.items import ListingItem


class TestSpider(scrapy.Spider):
    name = "test"
    start_urls = ["https://httpbin.org/json"]

    def parse(self, response: Response) -> ListingItem:
        yield ListingItem(
            url="https://example.com/test-listing",
            source="test",
            title="Test Listing — Kretinga",
            price=150000,
            area_sqm=120,
            rooms=4,
            address="Vilniaus g. 1",
            city="Kretinga",
            district="Kretinga",
            property_type="house",
            listing_type="sale",
            scraped_at=datetime.now(UTC).isoformat(),
        )
