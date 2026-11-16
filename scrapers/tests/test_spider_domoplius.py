"""Domoplius spider and the shared listing-spider base, against saved pages (no network).

Run inside the scraper container (stdlib unittest, no pytest in that image):
    docker compose exec scraper_worker python -m unittest discover -s tests

Fixtures in tests/fixtures/ are real domoplius pages trimmed to the embedded JSON the spider
reads, with every contact value replaced (see tests/fixtures/README.md). The pipelines run
here without a database.
"""

import asyncio
import html
import json
import re
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from scrapy import Request
from scrapy.http import HtmlResponse
from scrapy.utils.project import get_project_settings
from scrapy.utils.test import get_crawler

from realestate_spiders.pipelines import NormalizePipeline, ScrubPipeline, ValidatePipeline
from realestate_spiders.spiders.listings.base import ListingSpider
from realestate_spiders.spiders.listings.domoplius import DomopliusSpider

FIXTURES = Path(__file__).parent / "fixtures"
INDEX_URL = "https://domoplius.lt/skelbimai/namai-kotedzai-sodai/kretinga"
DETAIL_URL = (
    "https://domoplius.lt/skelbimai/"
    "parduodamas-gyvenamasis-namas-kretingos-r-sav-kretingoje-8816944.html"
)
# Contact values the fixtures carry in place of the real ones; none may reach an item.
FIXTURE_CONTACT_VALUES = ("REDACTED", "+370 600 00000", "redacted@example.invalid")


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def page_data(markup: str) -> dict:
    match = re.search(r'data-page="([^"]*)"', markup)
    return json.loads(html.unescape(match.group(1)))


def with_page_data(data: dict) -> str:
    attr = html.escape(json.dumps(data, ensure_ascii=False), quote=True)
    return f'<html><body><div id="app" data-page="{attr}"></div></body></html>'


def make_response(url: str, markup: str, **meta) -> HtmlResponse:
    request = Request(url, meta=meta)
    return HtmlResponse(url, body=markup.encode("utf-8"), encoding="utf-8", request=request)


def make_spider(**kwargs) -> DomopliusSpider:
    crawler = get_crawler(DomopliusSpider)
    return crawler._create_spider(**kwargs)


def collect_start(spider) -> list[Request]:
    async def run():
        return [r async for r in spider.start()]

    return asyncio.run(run())


def detail_item(mutate=None):
    """Parse the saved detail page (optionally after editing its JSON) into the one item."""
    data = page_data(read_fixture("domoplius_detail_house.html"))
    if mutate:
        mutate(data["props"]["property"])
    response = make_response(DETAIL_URL, with_page_data(data))
    items = list(make_spider().parse_detail(response))
    assert len(items) == 1, items
    return items[0]


class StartTest(unittest.TestCase):
    def test_starts_from_the_registry_url_without_playwright_or_bounds(self) -> None:
        requests = collect_start(make_spider())
        self.assertEqual([r.url for r in requests], [INDEX_URL])
        for request in requests:
            self.assertNotIn("bounds", request.url)
            self.assertFalse(request.meta.get("playwright"))

    def test_args_come_from_the_registry_and_can_be_overridden(self) -> None:
        spider = make_spider()
        self.assertEqual(spider.target, "house")
        self.assertEqual(spider.max_pages, 10)
        self.assertEqual(spider.region, "kretinga")
        self.assertEqual(make_spider(max_pages="1").max_pages, 1)

    def test_unknown_target_is_an_error(self) -> None:
        with self.assertRaises(ValueError):
            make_spider(target="castle")

    def test_playwright_download_handler_is_off_for_this_spider(self) -> None:
        settings = get_project_settings()
        DomopliusSpider.update_settings(settings)
        handlers = settings.getdict("DOWNLOAD_HANDLERS")
        for scheme in ("http", "https"):
            self.assertNotIn("playwright", handlers[scheme].lower())

    def test_spider_has_no_playwright_in_it(self) -> None:
        source = (
            Path(__file__).parent.parent
            / "realestate_spiders"
            / "spiders"
            / "listings"
            / "domoplius.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("playwright", source.lower())
        self.assertNotIn("bounds", source)

    def test_page_url_pages_by_query_and_leaves_page_one_alone(self) -> None:
        spider = make_spider()
        self.assertEqual(spider.page_url(INDEX_URL, 1), INDEX_URL)
        self.assertEqual(spider.page_url(INDEX_URL, 2), INDEX_URL + "?page=2")
        # `?page=1` redirects on domoplius, so a registry URL that carries it is cleaned.
        self.assertEqual(spider.page_url(INDEX_URL + "?page=1", 1), INDEX_URL)
        self.assertEqual(spider.page_url(INDEX_URL + "?page=1", 3), INDEX_URL + "?page=3")
        other = spider.page_url(INDEX_URL + "?x=1&page=2", 4)
        self.assertEqual(parse_qs(urlparse(other).query), {"x": ["1"], "page": ["4"]})


class IndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.markup = read_fixture("domoplius_index_kretinga_p1.html")
        self.spider = make_spider()

    def parse(self, markup=None, page=1, spider=None):
        response = make_response(INDEX_URL, markup or self.markup, index_url=INDEX_URL, page=page)
        return list((spider or self.spider).parse_index(response))

    def test_one_detail_request_per_listing_and_a_next_page(self) -> None:
        requests = self.parse()
        details = [r for r in requests if r.callback == self.spider.parse_detail]
        pages = [r for r in requests if r.callback == self.spider.parse_index]
        self.assertEqual(len(details), 20)
        self.assertEqual(len({r.url for r in details}), 20)
        for request in details:
            self.assertRegex(request.url, r"^https://domoplius\.lt/skelbimai/.+-\d+\.html$")
            self.assertFalse(request.meta.get("playwright"))
        self.assertEqual([r.url for r in pages], [INDEX_URL + "?page=2"])
        self.assertEqual(pages[0].meta["page"], 2)

    def test_counts_links_seen(self) -> None:
        self.parse()
        self.assertEqual(self.spider.crawler.stats.get_value("nt/links_seen"), 20)

    def test_stops_when_a_page_yields_no_new_links(self) -> None:
        self.parse()
        again = self.parse(page=2)  # the same 20 links come back, as past the last page
        self.assertEqual(again, [])
        self.assertEqual(self.spider.crawler.stats.get_value("nt/links_seen"), 20)

    def test_stops_on_an_empty_or_unreadable_page(self) -> None:
        self.assertEqual(self.parse("<html><body>nothing</body></html>"), [])
        data = page_data(self.markup)
        data["props"]["properties"] = []
        self.assertEqual(self.parse(with_page_data(data)), [])

    def test_max_pages_stops_the_walk(self) -> None:
        spider = make_spider(max_pages="1")
        requests = self.parse(spider=spider)
        self.assertEqual(len(requests), 20)  # the details, no next page
        self.assertTrue(all(r.callback == spider.parse_detail for r in requests))

    def test_only_sale_listings_are_followed(self) -> None:
        data = page_data(self.markup)
        data["props"]["properties"][0]["saleType"] = "rent"
        requests = self.parse(with_page_data(data))
        details = [r for r in requests if r.callback == self.spider.parse_detail]
        self.assertEqual(len(details), 19)
        self.assertNotIn("8816944", " ".join(r.url for r in details))

    def test_a_page_of_only_rentals_does_not_end_the_walk(self) -> None:
        data = page_data(self.markup)
        for prop in data["props"]["properties"]:
            prop["saleType"] = "rent"
        requests = self.parse(with_page_data(data))
        self.assertEqual([r.url for r in requests], [INDEX_URL + "?page=2"])
        stats = self.spider.crawler.stats
        self.assertEqual(stats.get_value("nt/links_seen"), 20)
        self.assertEqual(stats.get_value("nt/links_skipped"), 20)


class DetailTest(unittest.TestCase):
    def test_fields_come_from_the_embedded_json(self) -> None:
        item = detail_item()
        self.assertEqual(item["source"], "domoplius")
        self.assertEqual(item["source_id"], "8816944")
        self.assertEqual(item["url"], DETAIL_URL)
        self.assertEqual(item["title"], "Gyvenamasis namas Kretingos r. sav., Kretingoje")
        self.assertEqual(item["price"], "135000.00")
        self.assertEqual(item["area_sqm"], 134)
        self.assertEqual(item["plot_area_ares"], 8)
        self.assertEqual(item["rooms"], 7)
        self.assertEqual(item["year_built"], "1969")
        self.assertEqual(item["total_floors"], 2)
        self.assertEqual(item["property_type"], "house")
        self.assertEqual(item["listing_type"], "sale")
        self.assertEqual(item["city"], "Kretinga")
        self.assertEqual(item["address"], "Kretingos r. sav., Kretingoje")
        self.assertEqual(item["heating_type"], "Autonominis dujinis")
        self.assertAlmostEqual(item["latitude"], 55.883842)
        self.assertAlmostEqual(item["longitude"], 21.2350919)
        self.assertEqual(len(item["photo_urls"]), 10)

    def test_description_is_the_listings_own_text(self) -> None:
        item = detail_item()
        self.assertTrue(item["description"].startswith("Kretingoje parduodamas namas 1969m"))
        self.assertNotIn("kredit", item["description"].lower())

    def test_no_contact_data_in_the_item(self) -> None:
        item = detail_item()
        dumped = json.dumps(dict(item), ensure_ascii=False, default=str)
        for value in FIXTURE_CONTACT_VALUES:
            self.assertNotIn(value, dumped)
        for key in ("contact", "phone", "email", "seller"):
            self.assertNotIn(key, dumped.lower().replace("seller_type", ""))

    def test_new_build_is_not_guessed_from_year_keywords(self) -> None:
        def mutate(prop):
            prop["description"] = "Naujos statybos namas, 2025 m. baigtas, 2026 m. registruotas."

        item = detail_item(mutate)
        self.assertFalse(item.get("is_new_construction"))

    def test_town_level_coordinates_are_flagged_as_city_precision(self) -> None:
        # The fixture listing has no street: the portal's pin is the town, not the house.
        self.assertEqual(detail_item()["location_precision"], "city")

    def test_street_in_the_title_is_kept_and_flags_street_precision(self) -> None:
        def mutate(prop):
            prop["title"] = "Gyvenamojo namo dalis Kretingos r. sav., Kretingoje, Trakų g."
            prop["location"]["street"] = "Trakų g."

        item = detail_item(mutate)
        self.assertEqual(item["address"], "Kretingos r. sav., Kretingoje, Trakų g.")
        self.assertEqual(item["location_precision"], "street")

    def test_street_only_in_the_location_block_is_appended(self) -> None:
        def mutate(prop):
            prop["location"]["street"] = "Vilniaus g."

        self.assertEqual(
            detail_item(mutate)["address"], "Kretingos r. sav., Kretingoje, Vilniaus g."
        )

    def test_missing_coordinates_leave_geocoding_to_the_pipeline(self) -> None:
        def mutate(prop):
            prop["location"]["coordinates"] = None

        item = detail_item(mutate)
        self.assertNotIn("latitude", item)
        self.assertNotIn("longitude", item)
        self.assertNotIn("location_precision", item)

    def test_zero_coordinates_are_not_used(self) -> None:
        def mutate(prop):
            prop["location"]["coordinates"] = {"lat": 0, "lon": 0}

        self.assertNotIn("latitude", detail_item(mutate))

    def test_missing_optional_blocks_do_not_crash(self) -> None:
        def mutate(prop):
            for key in ("overview", "details", "location", "price", "images", "description"):
                prop.pop(key, None)

        item = detail_item(mutate)
        self.assertEqual(item["source_id"], "8816944")
        self.assertFalse(item.get("price"))
        self.assertEqual(item["address"], "Kretingos r. sav., Kretingoje")

    def test_page_without_embedded_json_yields_nothing(self) -> None:
        response = make_response(DETAIL_URL, "<html><body>Just a moment...</body></html>")
        self.assertEqual(list(make_spider().parse_detail(response)), [])

    def test_different_listings_do_not_share_a_description_or_price(self) -> None:
        def other(prop):
            prop["id"] = 1
            prop["description"] = "Kitas namas."
            prop["price"]["total"] = "99000.00"

        a, b = detail_item(), detail_item(other)
        self.assertNotEqual(a["description"], b["description"])
        self.assertNotEqual(a["price"], b["price"])

    def test_item_passes_the_listing_pipelines_without_a_database(self) -> None:
        spider = make_spider()
        item = detail_item()
        for pipeline in (ScrubPipeline(), ValidatePipeline(), NormalizePipeline()):
            item = pipeline.process_item(item, spider)
        self.assertEqual(item["price"], 135000.0)
        self.assertEqual(item["area_sqm"], 134.0)
        self.assertEqual(item["plot_area_ares"], 8.0)
        self.assertEqual(item["currency"], "EUR")


class BaseClassTest(unittest.TestCase):
    def test_domoplius_uses_the_shared_base(self) -> None:
        self.assertTrue(issubclass(DomopliusSpider, ListingSpider))

    def test_subclass_must_provide_the_two_hooks(self) -> None:
        class Bare(ListingSpider):
            name = "domoplius"  # a registered name, so the registry lookup works

        spider = Bare()
        response = make_response(INDEX_URL, "<html></html>")
        with self.assertRaises(NotImplementedError):
            spider.index_links(response)
        with self.assertRaises(NotImplementedError):
            list(spider.parse_detail(response))

    def test_unregistered_spider_name_is_an_error(self) -> None:
        class Stray(ListingSpider):
            name = "no-such-source"

        with self.assertRaises(ValueError):
            Stray()

    def test_each_start_url_is_walked_on_its_own(self) -> None:
        spider = make_spider()
        spider.start_urls = ("https://domoplius.lt/a", "https://domoplius.lt/b")
        urls = [r.url for r in collect_start(spider)]
        self.assertEqual(urls, ["https://domoplius.lt/a", "https://domoplius.lt/b"])


if __name__ == "__main__":
    unittest.main()
