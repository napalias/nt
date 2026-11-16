"""Capital spider against saved pages (no network).

Run inside the scraper container (stdlib unittest, no pytest in that image):
    docker compose exec scraper_worker python -m unittest discover -s tests

Fixtures in tests/fixtures/ come from the live site, trimmed to what the spider reads, with
every contact value replaced (see tests/fixtures/README.md). The pipelines run here without a
database.
"""

import asyncio
import json
import unittest
from pathlib import Path

from scrapy import Request
from scrapy.http import HtmlResponse
from scrapy.settings.default_settings import DOWNLOAD_HANDLERS_BASE
from scrapy.utils.project import get_project_settings
from scrapy.utils.test import get_crawler

from realestate_spiders.pipelines import NormalizePipeline, ScrubPipeline, ValidatePipeline
from realestate_spiders.spiders.listings.capital import CapitalSpider
from realestate_spiders.spiders.listings.contact_lines import is_contact_line, without_contact_lines

FIXTURES = Path(__file__).parent / "fixtures"
INDEX_URL = "https://www.capital.lt/lt/nekilnojamas-turtas/pirkti/kretingos-r-sav/namai"
DETAIL_URL = (
    "https://www.capital.lt/lt/parduodamas-namas-teresieciu-g-21-kretingoje-12722-kvm-ploto-2-"
    "aukstai-p356897"
)
LOG_HOUSE_URL = (
    "https://www.capital.lt/lt/parduodamas-namas-5-kambariu12156-kvadr-sodyba-su-42-aru-zeme-"
    "darbenuose-p284390"
)
# Contact values the fixtures carry in place of the real ones; none may reach an item.
FIXTURE_CONTACT_VALUES = ("Agentas Pavardenis", "+370 600 00000", "37060000000", "example.invalid")


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_response(url: str, markup: str, **meta) -> HtmlResponse:
    return HtmlResponse(
        url, body=markup.encode("utf-8"), encoding="utf-8", request=Request(url, meta=meta)
    )


def make_spider(**kwargs) -> CapitalSpider:
    return get_crawler(CapitalSpider)._create_spider(**kwargs)


def detail_item(name="capital_detail_house.html", url=DETAIL_URL, mutate=None):
    markup = read_fixture(name)
    if mutate:
        markup = mutate(markup)
    items = list(make_spider().parse_detail(make_response(url, markup)))
    assert len(items) == 1, items
    return items[0]


class StartTest(unittest.TestCase):
    def test_starts_from_the_registry_url_without_playwright(self) -> None:
        spider = make_spider()

        async def run():
            return [r async for r in spider.start()]

        requests = asyncio.run(run())
        self.assertEqual([r.url for r in requests], [INDEX_URL])
        self.assertFalse(requests[0].meta.get("playwright"))

    def test_args_come_from_the_registry_and_can_be_overridden(self) -> None:
        spider = make_spider()
        self.assertEqual(spider.target, "house")
        self.assertEqual(spider.max_pages, 3)
        self.assertEqual(spider.region, "kretinga")
        self.assertEqual(make_spider(max_pages="1").max_pages, 1)

    def test_playwright_download_handler_is_off_for_this_spider(self) -> None:
        settings = get_project_settings()
        CapitalSpider.update_settings(settings)
        handlers = settings.getdict("DOWNLOAD_HANDLERS")
        for scheme in ("http", "https"):
            self.assertEqual(handlers[scheme], DOWNLOAD_HANDLERS_BASE[scheme])

    def test_pages_by_query_and_leaves_page_one_alone(self) -> None:
        spider = make_spider()
        self.assertEqual(spider.page_url(INDEX_URL, 1), INDEX_URL)
        self.assertEqual(spider.page_url(INDEX_URL, 2), INDEX_URL + "?page=2")


class IndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.markup = read_fixture("capital_index_kretinga.html")
        self.spider = make_spider()

    def parse(self, markup=None, page=1, spider=None):
        response = make_response(INDEX_URL, markup or self.markup, index_url=INDEX_URL, page=page)
        return list((spider or self.spider).parse_index(response))

    def test_one_detail_request_per_house_and_a_next_page(self) -> None:
        requests = self.parse()
        details = [r for r in requests if r.callback == self.spider.parse_detail]
        pages = [r for r in requests if r.callback == self.spider.parse_index]
        self.assertEqual(len(details), 11)
        self.assertEqual(len({r.url for r in details}), 11)
        for request in details:
            self.assertRegex(
                request.url, r"^https://www\.capital\.lt/lt/parduodamas-namas-.+-p\d+$"
            )
            self.assertFalse(request.meta.get("playwright"))
        self.assertEqual([r.url for r in pages], [INDEX_URL + "?page=2"])

    def test_a_part_of_a_house_is_seen_but_not_followed(self) -> None:
        # The fixture's last card is "parduodama-namo-dalis-..." (a house share, not a house).
        self.parse()
        stats = self.spider.crawler.stats
        self.assertEqual(stats.get_value("nt/links_seen"), 12)
        self.assertEqual(stats.get_value("nt/links_skipped"), 1)

    def test_the_empty_second_page_ends_the_walk(self) -> None:
        page_two = "<html><body><h1>Parduodami namai</h1>rezultatai (12)</body></html>"
        self.parse()
        self.assertEqual(self.parse(page_two, page=2), [])

    def test_stops_when_a_page_repeats_the_same_links(self) -> None:
        self.parse()
        self.assertEqual(self.parse(page=2), [])

    def test_max_pages_stops_the_walk(self) -> None:
        spider = make_spider(max_pages="1")
        requests = self.parse(spider=spider)
        self.assertEqual([r for r in requests if r.callback == spider.parse_index], [])
        self.assertEqual(len([r for r in requests if r.callback == spider.parse_detail]), 11)

    def test_rental_cards_are_not_followed(self) -> None:
        markup = self.markup.replace("/lt/parduodamas-namas-geniu", "/lt/nuomojamas-namas-geniu")
        self.parse(markup)
        self.assertEqual(self.spider.crawler.stats.get_value("nt/links_skipped"), 2)

    def test_no_contact_data_in_the_requests(self) -> None:
        for request in self.parse():
            self.assertNotIn("tel:", request.url)


class DetailTest(unittest.TestCase):
    def test_fields_from_the_listing_page(self) -> None:
        item = detail_item()
        self.assertEqual(item["source"], "capital")
        self.assertEqual(item["source_id"], "356897")
        self.assertEqual(item["url"], DETAIL_URL)
        self.assertEqual(
            item["title"],
            "Parduodamas namas Teresiečių g. 21, Kretingoje, 127.22 kv.m ploto, 2 aukštai",
        )
        self.assertEqual(item["price"], "299900")  # the comma is a thousands separator
        self.assertEqual(item["area_sqm"], 127.22)
        self.assertEqual(item["rooms"], 4)
        self.assertEqual(item["total_floors"], 2)
        self.assertEqual(item["year_built"], "2025")
        self.assertEqual(item["building_type"], "brick")
        self.assertEqual(item["heating_type"], "Dujos")
        self.assertEqual(item["address"], "Teresiečių g., Kretingos r. sav., Kretingos m.")
        self.assertEqual(item["property_type"], "house")
        self.assertEqual(item["listing_type"], "sale")

    def test_log_house_with_comma_decimals_in_the_title(self) -> None:
        item = detail_item("capital_detail_darbenai.html", LOG_HOUSE_URL)
        self.assertEqual(item["source_id"], "284390")
        self.assertEqual(item["price"], "129000")
        self.assertEqual(item["area_sqm"], 121.56)
        self.assertEqual(item["rooms"], 5)
        self.assertEqual(item["building_type"], "log")
        self.assertEqual(item["address"], "Palangos g., Kretingos r. sav., Darbėnų mstl.")

    def test_every_item_has_a_source_url(self) -> None:
        for name, url in (
            ("capital_detail_house.html", DETAIL_URL),
            ("capital_detail_darbenai.html", LOG_HOUSE_URL),
        ):
            with self.subTest(url=url):
                self.assertEqual(detail_item(name, url)["url"], url)

    def test_description_is_the_listings_text_without_the_short_link(self) -> None:
        item = detail_item()
        self.assertTrue(item["description"].startswith("PARDUODAMAS MODERNUS NAMAS"))
        self.assertIn("Šildomos grindys visame name", item["description"])
        self.assertNotIn("Trumpa nuoroda", item["description"])
        self.assertNotIn("capital.lt/p", item["description"])

    def test_photos_come_from_the_gallery_json(self) -> None:
        item = detail_item()
        self.assertEqual(len(item["photo_urls"]), 4)
        for url in item["photo_urls"]:
            self.assertTrue(url.startswith("https://static.capital.lt/files/"))

    def test_no_contact_data_survives_the_scrub_pipeline(self) -> None:
        item = detail_item()
        # The spider never reads the broker block; the scrub pipeline is the second guard.
        spider = make_spider()
        scrubbed = ScrubPipeline().process_item(item, spider)
        dumped = json.dumps(dict(scrubbed), ensure_ascii=False, default=str)
        for value in ("+370 600 00000", "37060000000", "example.invalid"):
            self.assertNotIn(value, dumped)
        for key in ("tel:", "mailto:", "broker"):
            self.assertNotIn(key, dumped.lower())

    def test_the_contact_line_of_the_description_goes_whole_with_the_agent_name(self) -> None:
        item = detail_item()
        self.assertNotIn("Agentas Pavardenis", json.dumps(dict(item), ensure_ascii=False))
        self.assertNotIn("kreipkitės", item["description"])
        self.assertIn("II aukšte – 3 miegamieji", item["description"])  # the line before it stays

    def test_a_rental_page_yields_nothing(self) -> None:
        markup = read_fixture("capital_detail_house.html").replace(
            '<td colspan="3">Pardavimas</td>', '<td colspan="3">Nuoma</td>'
        )
        response = make_response(DETAIL_URL, markup)
        self.assertEqual(list(make_spider().parse_detail(response)), [])

    def test_missing_optional_blocks_do_not_crash(self) -> None:
        def strip(markup):
            return markup.replace("realty-info-line", "x").replace("realty-main-info-top", "y")

        item = detail_item(mutate=strip)
        self.assertEqual(item["source_id"], "356897")
        self.assertEqual(item["address"], "")
        self.assertIsNone(item.get("area_sqm"))

    def test_page_that_is_not_a_listing_yields_nothing(self) -> None:
        response = make_response(DETAIL_URL, "<html><body>Just a moment...</body></html>")
        self.assertEqual(list(make_spider().parse_detail(response)), [])

    def test_item_passes_the_listing_pipelines_without_a_database(self) -> None:
        spider = make_spider()
        item = detail_item()
        for pipeline in (ScrubPipeline(), ValidatePipeline(), NormalizePipeline()):
            item = pipeline.process_item(item, spider)
        # 299,900 reaches the normalizer as 299900 (it would read "299,900" as 299.9).
        self.assertEqual(item["price"], 299900.0)
        self.assertEqual(item["area_sqm"], 127.22)
        self.assertEqual(item["rooms"], 4)
        self.assertNotIn("+370", item["description"])


class ContactLinesTest(unittest.TestCase):
    def test_a_contact_request_with_a_number_is_dropped_whole(self) -> None:
        self.assertTrue(is_contact_line("Dėl apžiūros kreipkitės – Vardas +370 600 00000"))
        self.assertTrue(is_contact_line("Tel. 8 600 00000, vardas@example.invalid"))

    def test_a_number_without_a_contact_cue_is_left_to_the_scrub_pipeline(self) -> None:
        line = "Namas pastatytas 1969 m., kaina 135 000 €, plotas 134 m2"
        self.assertFalse(is_contact_line(line))
        self.assertFalse(is_contact_line("Skambinkite bet kada"))  # a cue without a number
        self.assertEqual(without_contact_lines([line, "Kreipkitės +370 600 00000"]), [line])


if __name__ == "__main__":
    unittest.main()
