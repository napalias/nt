"""Alginos NT spider against saved pages (no network).

docker compose exec scraper_worker python -m unittest discover -s tests
"""

import unittest

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

from realestate_spiders.spiders.listings.alginosnt import AlginosntSpider
from realestate_spiders.spiders.listings.base import ListingSpider

INDEX_URL = "https://alginosnt.lt/search?itemType=namas&region=25"
DETAIL_URL = "https://alginosnt.lt/nekilnojamas-turtas/skelbimas/12083"
LISTING_ROOT = "https://alginosnt.lt/nekilnojamas-turtas/skelbimas/"


def spider(**kwargs) -> AlginosntSpider:
    return make_spider(AlginosntSpider, **kwargs)


def parse_index(sp, markup=None, page=1):
    markup = markup or read_fixture("alginosnt_index_kretingos_r.html")
    response = make_response(INDEX_URL, markup, index_url=INDEX_URL, page=page)
    return list(sp.parse_index(response))


def detail_item(mutate=None):
    markup = read_fixture("alginosnt_detail_house.html")
    if mutate:
        markup = mutate(markup)
    items = list(spider().parse_detail(make_response(DETAIL_URL, markup)))
    assert len(items) == 1, items
    return items[0]


class StartTest(unittest.TestCase):
    def test_uses_the_shared_base_and_the_district_filter_url(self) -> None:
        self.assertTrue(issubclass(AlginosntSpider, ListingSpider))
        self.assertEqual([r.url for r in collect_start(spider())], [INDEX_URL])
        self.assertEqual(spider().target, "house")
        self.assertEqual(spider().region, "kretinga")

    def test_pages_are_added_to_the_filter_query(self) -> None:
        sp = spider()
        self.assertEqual(sp.page_url(INDEX_URL, 1), INDEX_URL)
        self.assertEqual(sp.page_url(INDEX_URL, 2), INDEX_URL + "&page=2")

    def test_no_browser_is_started(self) -> None:
        handlers = AlginosntSpider.custom_settings["DOWNLOAD_HANDLERS"]
        for scheme in ("http", "https"):
            self.assertNotIn("playwright", handlers[scheme].lower())


class IndexTest(unittest.TestCase):
    def test_one_detail_request_per_house_read_from_the_onclick(self) -> None:
        sp = spider()
        details, pages = split_requests(sp, parse_index(sp))
        # Cards are not links, and each has a mobile twin: 3 houses, 6 card divs.
        self.assertEqual(
            sorted(r.url for r in details),
            [LISTING_ROOT + "11845", LISTING_ROOT + "12026", LISTING_ROOT + "12083"],
        )
        self.assertEqual(pages, [])  # no pager: one page
        self.assertEqual(sp.crawler.stats.get_value("nt/links_seen"), 3)

    def test_next_page_comes_from_the_pager(self) -> None:
        markup = read_fixture("alginosnt_index_kretingos_r.html").replace(
            "</body>",
            '<a href="https://alginosnt.lt/search?itemType=namas&amp;region=25&amp;page=2">2</a>'
            "</body>",
        )
        sp = spider()
        _, pages = split_requests(sp, parse_index(sp, markup))
        self.assertEqual([r.url for r in pages], [INDEX_URL + "&page=2"])

    def test_the_last_page_does_not_ask_for_another(self) -> None:
        markup = read_fixture("alginosnt_index_kretingos_r.html").replace(
            "</body>", '<a href="/search?region=25&amp;page=2">2</a></body>'
        )
        sp = spider()
        _, pages = split_requests(sp, parse_index(sp, markup, page=2))
        self.assertEqual(pages, [])  # the pager only links back to page 2

    def test_max_pages_stops_the_walk(self) -> None:
        markup = read_fixture("alginosnt_index_kretingos_r.html").replace(
            "</body>", '<a href="/search?region=25&amp;page=2">2</a></body>'
        )
        sp = spider(max_pages="1")
        self.assertEqual(split_requests(sp, parse_index(sp, markup))[1], [])

    def test_empty_result_yields_nothing(self) -> None:
        self.assertEqual(parse_index(spider(), "<html><body>Nieko nerasta</body></html>"), [])


class DetailTest(unittest.TestCase):
    def test_fields_come_from_the_info_table(self) -> None:
        item = detail_item()
        self.assertEqual(item["source"], "alginosnt")
        self.assertEqual(item["source_id"], "12083")
        self.assertEqual(item["url"], DETAIL_URL)
        self.assertEqual(item["title"], "Namas, Joskaudų g., Lazdininkų k.")
        self.assertEqual(item["property_type"], "house")
        self.assertEqual(item["listing_type"], "sale")
        self.assertEqual(item["price"], 79000)
        self.assertEqual(item["area_sqm"], 120)
        self.assertEqual(item["plot_area_ares"], 32)
        self.assertEqual(item["rooms"], 5)
        self.assertEqual(item["year_built"], 1977)
        self.assertEqual(item["total_floors"], 2)  # "Aukštas: 0/2"
        self.assertEqual(item["building_type"], "brick")
        self.assertEqual(item["heating_type"], "Kietu kuru, Kita")
        self.assertEqual(item["address"], "Joskaudų g., Lazdininkų k., Kretingos r. sav.")
        self.assertEqual(item["city"], "Lazdininkų k.")
        self.assertEqual(item["municipality"], "Kretingos r. sav.")
        self.assertEqual(item["photo_urls"], [])  # the saved listing has no photos

    def test_photos_come_from_the_gallery_script(self) -> None:
        def mutate(markup):
            return markup.replace(
                "const images = [  ];",
                'const images = [ "https://alginosnt.lt/storage/skelbimai/a.jpg", '
                '"https://alginosnt.lt/storage/skelbimai/b.jpg" ];',
            )

        self.assertEqual(
            detail_item(mutate)["photo_urls"],
            [
                "https://alginosnt.lt/storage/skelbimai/a.jpg",
                "https://alginosnt.lt/storage/skelbimai/b.jpg",
            ],
        )

    def test_description_is_the_listings_own_text(self) -> None:
        text = detail_item()["description"]
        self.assertTrue(text.startswith("Parduodamas gyvenamasis namas Lazdininkų km."))
        self.assertIn("du atskirus gyvenamuosius vienetus", text)

    def test_no_contact_data_reaches_the_item(self) -> None:
        # The fixture really carries the redacted agent box, so this test has teeth.
        self.assertIn("Agentas Pavardenis", read_fixture("alginosnt_detail_house.html"))
        blob = dumped(detail_item())
        for value in PLACEHOLDERS:
            self.assertNotIn(value, blob)
        self.assertNotIn("@", blob)

    def test_page_without_a_listing_yields_nothing(self) -> None:
        response = make_response(DETAIL_URL, "<html><body>404</body></html>")
        self.assertEqual(list(spider().parse_detail(response)), [])

    def test_missing_rows_do_not_crash(self) -> None:
        def mutate(markup):
            for label in ("Kaina:", "Plotas:", "Aukštas:", "Pastato tipas:"):
                markup = markup.replace(f"<td>{label}</td>", "<td>Kita:</td>")
            return markup

        item = detail_item(mutate)
        self.assertEqual(item["source_id"], "12083")
        self.assertFalse(item.get("price"))
        self.assertNotIn("total_floors", item)

    def test_item_passes_the_listing_pipelines(self) -> None:
        item = run_pipelines(spider(), detail_item())
        self.assertEqual(item["price"], 79000.0)
        self.assertEqual(item["currency"], "EUR")


if __name__ == "__main__":
    unittest.main()
