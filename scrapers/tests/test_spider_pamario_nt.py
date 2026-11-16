"""Pamario NT spider against saved pages (no network).

    docker compose exec scraper_worker python -m unittest discover -s tests

The Kretinga houses page holds one object, so the site redirects it to that object's page; the
list shape comes from the agency's Klaipeda houses page (see tests/fixtures/README.md).
"""

import unittest

from apps.listings.sources import get_source
from spider_helpers import (
    PLACEHOLDERS,
    collect_start,
    dumped,
    make_response,
    make_spider,
    read_fixture,
    run_pipelines,
    split_requests,
)

from realestate_spiders.items import ListingItem
from realestate_spiders.spiders.listings.base import ListingSpider
from realestate_spiders.spiders.listings.pamario_nt import PamarioNtSpider

START_URL = "https://www.pamario-nt.lt/lt/kretinga/namai---"
DETAIL_URL = "https://www.pamario-nt.lt/lt/nta/37229-namas-kretinga"
LIST_URL = "https://www.pamario-nt.lt/lt/klaipeda/klpnamai"


def spider(**kwargs) -> PamarioNtSpider:
    return make_spider(PamarioNtSpider, **kwargs)


def detail_markup() -> str:
    return read_fixture("pamario_nt_detail_house.html")


def detail_item(markup=None):
    response = make_response(DETAIL_URL, markup or detail_markup())
    items = list(spider().parse_detail(response))
    assert len(items) == 1, items
    return items[0]


class StartTest(unittest.TestCase):
    def test_uses_the_shared_base_and_the_registry_url(self) -> None:
        self.assertTrue(issubclass(PamarioNtSpider, ListingSpider))
        self.assertEqual([r.url for r in collect_start(spider())], [START_URL])
        self.assertEqual(spider().target, "house")

    def test_honours_the_robots_crawl_delay_of_10_seconds(self) -> None:
        self.assertEqual(PamarioNtSpider.custom_settings["DOWNLOAD_DELAY"], 10)
        self.assertEqual(get_source("pamario-nt").delay_s, 10)

    def test_no_browser_is_started(self) -> None:
        handlers = PamarioNtSpider.custom_settings["DOWNLOAD_HANDLERS"]
        for scheme in ("http", "https"):
            self.assertNotIn("playwright", handlers[scheme].lower())


class IndexTest(unittest.TestCase):
    def parse(self, markup=None, sp=None, url=LIST_URL):
        sp = sp or spider()
        markup = markup or read_fixture("pamario_nt_index_klaipeda_houses.html")
        response = make_response(url, markup, index_url=url, page=1)
        return sp, list(sp.parse_index(response))

    def test_one_detail_request_per_card_and_no_next_page_when_all_are_shown(self) -> None:
        sp, requests = self.parse()
        details, pages = split_requests(sp, requests)
        self.assertEqual(len(details), 5)
        for request in details:
            self.assertRegex(request.url, r"^https://www\.pamario-nt\.lt/lt/nta/\d+-namas-")
        self.assertEqual(pages, [])  # "Rasti: 5 objektai" and 5 cards
        self.assertEqual(sp.crawler.stats.get_value("nt/links_seen"), 5)

    def test_asks_for_a_next_page_only_when_more_objects_exist_than_cards(self) -> None:
        markup = read_fixture("pamario_nt_index_klaipeda_houses.html").replace(
            '<span class="paieska_filter_blue">5</span>',
            '<span class="paieska_filter_blue">12</span>',
        )
        sp, requests = self.parse(markup)
        _, pages = split_requests(sp, requests)
        self.assertEqual([r.url for r in pages], [LIST_URL + "?page=2"])

    def test_max_pages_stops_the_walk(self) -> None:
        markup = read_fixture("pamario_nt_index_klaipeda_houses.html").replace(
            '<span class="paieska_filter_blue">5</span>',
            '<span class="paieska_filter_blue">12</span>',
        )
        sp, requests = self.parse(markup, spider(max_pages="1"))
        self.assertEqual(split_requests(sp, requests)[1], [])

    def test_empty_category_yields_nothing(self) -> None:
        markup = (
            '<div class="paieska_filter">Rasti: <span class="paieska_filter_blue">0</span></div>'
        )
        _, requests = self.parse(markup)
        self.assertEqual(requests, [])

    def test_a_single_object_redirect_is_read_as_the_listing(self) -> None:
        # /lt/kretinga/namai--- answers with the one house's own page.
        sp = spider()
        response = make_response(DETAIL_URL, detail_markup(), index_url=START_URL, page=1)
        out = list(sp.parse_index(response))
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], ListingItem)
        self.assertEqual(out[0]["url"], DETAIL_URL)
        self.assertEqual(sp.crawler.stats.get_value("nt/links_seen"), 1)


class DetailTest(unittest.TestCase):
    def test_fields(self) -> None:
        item = detail_item()
        self.assertEqual(item["source"], "pamario-nt")
        self.assertEqual(item["source_id"], "37229")
        self.assertEqual(item["url"], DETAIL_URL)
        self.assertEqual(item["title"], "Namas pardavimui, Kretinga")
        self.assertEqual(item["property_type"], "house")
        self.assertEqual(item["listing_type"], "sale")
        self.assertEqual(item["price"], 83000)
        self.assertEqual(item["area_sqm"], 49)
        self.assertEqual(item["plot_area_ares"], 2)
        self.assertEqual(item["rooms"], 3)
        self.assertEqual(item["year_built"], 1970)
        self.assertEqual(item["total_floors"], 2)
        self.assertEqual(item["building_type"], "brick")
        self.assertEqual(item["heating_type"], "Kietu kuru")
        self.assertEqual(item["address"], "Kretinga, Kretingos r. sav.")
        self.assertEqual(item["city"], "Kretinga")
        self.assertEqual(item["municipality"], "Kretingos r. sav.")

    def test_photos_resolve_against_the_sites_base_href(self) -> None:
        photos = detail_item()["photo_urls"]
        self.assertGreater(len(photos), 3)
        for url in photos:
            self.assertTrue(url.startswith("https://www.pamario-nt.lt/images_mod/"), url)

    def test_description_is_the_listings_text_without_the_agencys_closing_paragraph(self) -> None:
        text = detail_item()["description"]
        self.assertTrue(text.startswith("Parduodama namo dalis Kretingoje"))
        self.assertIn("garažas su duobe", text)
        for phrase in ("Pamario", "Skambinkite", "laukiame skambučio", "bankais", "____"):
            self.assertNotIn(phrase, text)

    def test_no_contact_data_reaches_the_item(self) -> None:
        # The fixture really carries the redacted broker box, so this test has teeth.
        self.assertIn("Agentas Pavardenis", detail_markup())
        blob = dumped(detail_item())
        for value in PLACEHOLDERS:
            self.assertNotIn(value, blob)
        self.assertNotIn("@", blob)

    def test_a_rental_is_not_an_item(self) -> None:
        markup = detail_markup().replace("Namas pardavimui", "Namas nuomai")
        response = make_response(DETAIL_URL, markup)
        self.assertEqual(list(spider().parse_detail(response)), [])

    def test_page_without_a_listing_yields_nothing(self) -> None:
        response = make_response(DETAIL_URL, "<html><body>Not found</body></html>")
        self.assertEqual(list(spider().parse_detail(response)), [])

    def test_item_passes_the_listing_pipelines(self) -> None:
        item = run_pipelines(spider(), detail_item())
        self.assertEqual(item["price"], 83000.0)
        self.assertEqual(item["currency"], "EUR")


if __name__ == "__main__":
    unittest.main()
