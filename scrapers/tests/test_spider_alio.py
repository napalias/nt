"""Alio spider against saved pages (no network).

Run inside the scraper container (stdlib unittest, no pytest in that image):
    docker compose exec scraper_worker python -m unittest discover -s tests

Fixtures in tests/fixtures/ are real alio pages trimmed to the sections the spider reads, with
every contact value replaced (see tests/fixtures/README.md).
"""

import json
import re
import unittest

from scrapy.utils.project import get_project_settings
from spider_helpers import (
    PLACEHOLDER_EMAIL,
    PLACEHOLDER_PHONE,
    SPIDERS,
    collect_start,
    details_of,
    make_response,
    make_spider,
    read_fixture,
)

from realestate_spiders.pipelines import NormalizePipeline, ScrubPipeline, ValidatePipeline
from realestate_spiders.spiders.listings.alio import AlioSpider
from realestate_spiders.spiders.listings.base import ListingSpider

INDEX_URL = "https://www.alio.lt/paieska/kretingos-r-sav/?category_id=1433"
PAGE2_URL = "https://www.alio.lt/paieska/kretingos-r-sav/2/?category_id=1433"
HOUSE_URL = "https://www.alio.lt/skelbimai/sodo-namas-kretingos-r.-sav.-daubenai/ID69851715.html"
PART_URL = (
    "https://www.alio.lt/skelbimai/parduodama-namo-dalis-1-as-aukstas-su-22aru-sklypu./"
    "ID69532877.html"
)


def spider(**kwargs) -> AlioSpider:
    return make_spider(AlioSpider, **kwargs)


def detail_response(fixture="alio_detail_house.html", url=HOUSE_URL, edit=None):
    markup = read_fixture(fixture)
    if edit:
        markup = edit(markup)
    return make_response(url, markup)


def detail_item(fixture="alio_detail_house.html", url=HOUSE_URL, edit=None):
    items = details_of(spider(), detail_response(fixture, url, edit))
    assert len(items) == 1, items
    return items[0]


class StartTest(unittest.TestCase):
    def test_starts_from_the_registry_url_without_a_browser(self) -> None:
        requests = collect_start(spider())
        self.assertEqual([r.url for r in requests], [INDEX_URL])
        self.assertFalse(requests[0].meta.get("playwright"))

    def test_args_come_from_the_registry_and_can_be_overridden(self) -> None:
        s = spider()
        self.assertEqual(s.target, "house")
        self.assertEqual(s.max_pages, 5)
        self.assertEqual(s.region, "kretinga")
        self.assertEqual(spider(max_pages="1").max_pages, 1)

    def test_page_is_a_path_segment_before_the_query(self) -> None:
        s = spider()
        self.assertEqual(s.page_url(INDEX_URL, 1), INDEX_URL)
        self.assertEqual(s.page_url(INDEX_URL, 2), PAGE2_URL)
        self.assertEqual(
            s.page_url(INDEX_URL, 7),
            "https://www.alio.lt/paieska/kretingos-r-sav/7/?category_id=1433",
        )
        # a registry URL that already carries a page walks from that page's base
        self.assertEqual(s.page_url(PAGE2_URL, 1), INDEX_URL)
        self.assertEqual(s.page_url(PAGE2_URL, 3), s.page_url(INDEX_URL, 3))

    def test_every_request_goes_through_curl_cffi_not_a_browser(self) -> None:
        settings = get_project_settings()
        AlioSpider.update_settings(settings)
        handlers = settings.getdict("DOWNLOAD_HANDLERS")
        for scheme in ("http", "https"):
            self.assertEqual(
                handlers[scheme], "realestate_spiders.handlers.CurlCffiDownloadHandler"
            )
        self.assertNotIn("playwright", (SPIDERS / "alio.py").read_text(encoding="utf-8").lower())

    def test_uses_the_shared_base(self) -> None:
        self.assertTrue(issubclass(AlioSpider, ListingSpider))


class IndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.markup = read_fixture("alio_index_kretinga_p1.html")
        self.spider = spider()

    def parse(self, markup=None, page=1, s=None):
        response = make_response(INDEX_URL, markup or self.markup, index_url=INDEX_URL, page=page)
        return list((s or self.spider).parse_index(response))

    def test_one_detail_request_per_card_and_a_next_page(self) -> None:
        requests = self.parse()
        details = [r for r in requests if r.callback == self.spider.parse_detail]
        pages = [r for r in requests if r.callback == self.spider.parse_index]
        self.assertEqual(len(details), 24)
        self.assertEqual(len({r.url for r in details}), 24)
        for request in details:
            self.assertRegex(request.url, r"^https://www\.alio\.lt/skelbimai/[^/]+/ID\d+\.html$")
            self.assertFalse(request.meta.get("playwright"))
        self.assertEqual([r.url for r in pages], [PAGE2_URL])
        self.assertEqual(pages[0].meta["page"], 2)

    def test_counts_links_seen(self) -> None:
        self.parse()
        self.assertEqual(self.spider.crawler.stats.get_value("nt/links_seen"), 24)

    def test_stops_when_a_page_yields_no_new_links(self) -> None:
        self.parse()
        self.assertEqual(self.parse(page=2), [])

    def test_stops_on_an_empty_page(self) -> None:
        self.assertEqual(self.parse("<html><body>Nieko nerasta</body></html>"), [])

    def test_max_pages_stops_the_walk(self) -> None:
        s = spider(max_pages="1")
        requests = self.parse(s=s)
        self.assertEqual(len(requests), 24)
        self.assertTrue(all(r.callback == s.parse_detail for r in requests))

    def test_only_card_links_of_the_listing_shape_are_followed(self) -> None:
        extra = (
            # the old, never-matching shape, and other pages of the site
            '<a class="main_a_c_b" href="/nekilnojamas-turtas/namai/kretinga-123456.html">x</a>'
            '<a class="main_a_c_b" href="/nekilnojamas-turtas/kretingos-r-sav/page/2.html">x</a>'
            '<a class="main_a_c_b" href="/skelbimai/kategorija/">x</a>'
            # a listing-shaped link that is not a result card
            '<a href="/skelbimai/namas-kretingoje/ID99999999.html">banner</a>'
        )
        requests = self.parse(self.markup.replace("</body>", extra + "</body>"))
        details = [r.url for r in requests if r.callback == self.spider.parse_detail]
        self.assertEqual(len(details), 24)
        self.assertFalse([u for u in details if "99999999" in u or "123456" in u])

    def test_query_and_fragment_are_dropped_from_listing_links(self) -> None:
        card = '<a class="main_a_c_b" href="/skelbimai/namas-x/ID5.html?utm_source=a#top">x</a>'
        requests = self.parse(f"<html><body>{card}</body></html>")
        self.assertEqual(
            [r.url for r in requests if r.callback == self.spider.parse_detail],
            ["https://www.alio.lt/skelbimai/namas-x/ID5.html"],
        )


class DetailTest(unittest.TestCase):
    def test_fields_come_from_the_page(self) -> None:
        item = detail_item()
        self.assertEqual(item["source"], "alio")
        self.assertEqual(item["source_id"], "69851715")
        self.assertEqual(item["url"], HOUSE_URL)
        self.assertEqual(item["title"], "Sodo namas Kretingos r. sav., Daubėnai")
        self.assertEqual(item["price"], "34500.00")
        self.assertEqual(item["area_sqm"], 66.39)
        self.assertEqual(item["plot_area_ares"], 9.0)
        self.assertEqual(item["rooms"], 3)
        self.assertEqual(item["total_floors"], 1)
        self.assertEqual(item["year_built"], 2018)
        self.assertEqual(item["building_type"], "block")
        self.assertEqual(item["property_type"], "house")
        self.assertEqual(item["listing_type"], "sale")
        self.assertEqual(item["address"], "Kretingos r. sav., Daubėnai")
        self.assertEqual(item["city"], "Daubėnai")
        self.assertEqual(len(item["photo_urls"]), 5)
        self.assertTrue(all(u.startswith("https://s.alio.lt/photos/") for u in item["photo_urls"]))
        self.assertEqual(item["raw_data"]["facts"]["pastato tipas"], "Sodo namas")

    def test_description_is_only_the_listings_text_with_line_breaks(self) -> None:
        item = detail_item()
        lines = item["description"].split("\n")
        self.assertTrue(lines[0].startswith("Ieškote ramybės, gamtos ir vietos"))
        self.assertIn("Plotas: 9 arai", lines)
        # not "all page text over 40 characters": nothing from the page chrome
        for chrome in ("Skambinti", "Siūlyti kainą", "Reklama", "Nusikalstamumas"):
            self.assertNotIn(chrome, item["description"])

    def test_no_contact_data_in_the_item(self) -> None:
        item = detail_item()
        dumped = json.dumps(dict(item), ensure_ascii=False, default=str).lower()
        for key in ("av2-contact", "tel:", "privatus asmuo", "nt brokeris"):
            self.assertNotIn(key, dumped)

    def test_phone_typed_into_the_description_is_removed_by_the_scrub_pipeline(self) -> None:
        item = detail_item()
        self.assertIn(
            PLACEHOLDER_PHONE, item["description"]
        )  # the spider leaves it to the pipeline
        item = ScrubPipeline().process_item(item, spider())
        self.assertNotIn("600 00000", item["description"])
        self.assertNotIn(PLACEHOLDER_EMAIL, item["description"])

    def test_pin_and_its_precision_are_taken_from_the_map(self) -> None:
        item = detail_item("alio_detail_house_part_pin.html", PART_URL)
        self.assertAlmostEqual(item["latitude"], 55.84947)
        self.assertAlmostEqual(item["longitude"], 21.47084)
        # the page says the pin is the town, so the item must not claim more
        self.assertEqual(item["location_precision"], "city")
        self.assertEqual(item["city"], "Žutautai")
        self.assertEqual(item["area_sqm"], 56.0)
        self.assertEqual(item["plot_area_ares"], 22.0)
        self.assertEqual(item["heating_type"], "Kietu kuru")
        self.assertEqual(item["description"], "")  # this ad has no description block

    def test_an_unknown_pin_precision_is_not_called_exact(self) -> None:
        def edit(markup):
            return markup.replace('data-precision="city"', 'data-precision="whatever"')

        item = detail_item("alio_detail_house_part_pin.html", PART_URL, edit)
        self.assertEqual(item["location_precision"], "city")

    def test_without_a_pin_the_geocoder_decides(self) -> None:
        item = detail_item()
        self.assertNotIn("latitude", item)
        self.assertNotIn("location_precision", item)

    def test_a_street_in_the_place_line_flags_street_precision(self) -> None:
        def edit(markup):
            return markup.replace(
                "<span>Kretingos r. sav., Daubėnai</span>",
                "<span>Kretingos r. sav., Kretinga, Šventosios g.</span>",
                1,
            )

        item = detail_item(edit=edit)
        self.assertEqual(item["address"], "Kretingos r. sav., Kretinga, Šventosios g.")
        self.assertEqual(item["city"], "Kretinga")
        self.assertEqual(item["location_precision"], "street")

    def test_a_town_pin_outside_lithuania_is_ignored(self) -> None:
        def edit(markup):
            return markup.replace('data-lat="55.84947"', 'data-lat="0"')

        self.assertNotIn("latitude", detail_item("alio_detail_house_part_pin.html", PART_URL, edit))

    def test_price_falls_back_to_the_price_block(self) -> None:
        def edit(markup):
            return re.sub(r'<meta name="og:price"[^>]*>', "", markup)

        self.assertEqual(detail_item(edit=edit)["price"], 34500.0)

    def test_item_passes_the_listing_pipelines_without_a_database(self) -> None:
        s = spider()
        item = detail_item()
        for pipeline in (ScrubPipeline(), ValidatePipeline(), NormalizePipeline()):
            item = pipeline.process_item(item, s)
        self.assertEqual(item["price"], 34500.0)
        self.assertEqual(item["area_sqm"], 66.39)
        self.assertEqual(item["currency"], "EUR")


class SkipTest(unittest.TestCase):
    def parse(self, edit):
        s = spider()
        return s, details_of(s, detail_response(edit=edit))

    def assert_skipped(self, edit) -> None:
        s, items = self.parse(edit)
        self.assertEqual(items, [])
        self.assertEqual(s.crawler.stats.get_value("nt/details_skipped"), 1)

    def test_a_rental_is_skipped(self) -> None:
        self.assert_skipped(lambda m: m.replace('"name": "Parduoda"', '"name": "Nuomoja"'))

    def test_another_category_is_skipped(self) -> None:
        self.assert_skipped(lambda m: m.replace("namai-sodybos-kotedzai", "butai"))

    def test_a_page_without_breadcrumbs_is_not_trusted(self) -> None:
        self.assert_skipped(lambda m: m.replace('"BreadcrumbList"', '"Other"'))

    def test_a_page_that_is_not_a_listing_yields_nothing(self) -> None:
        s = spider()
        response = make_response(HOUSE_URL, "<html><body>Just a moment...</body></html>")
        self.assertEqual(details_of(s, response), [])
        self.assertIsNone(s.crawler.stats.get_value("nt/details_skipped"))

    def test_a_non_listing_url_yields_nothing(self) -> None:
        response = detail_response(url="https://www.alio.lt/nekilnojamas-turtas/namai-123.html")
        self.assertEqual(details_of(spider(), response), [])


if __name__ == "__main__":
    unittest.main()
