"""Meguvos bustas spider against saved pages (no network).

    docker compose exec scraper_worker python -m unittest discover -s tests

The fixtures are real pages, trimmed and redacted (tests/fixtures/README.md).
"""

import re
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

from realestate_spiders.spiders.listings.base import ListingSpider
from realestate_spiders.spiders.listings.meguvosbustas import MeguvosbustasSpider, site_url

INDEX_URL = "https://meguvosbustas.lt/paieska"
VILLAGE_URL = "https://meguvosbustas.lt/parduodami-namai/Kretingos+r./5-kambariu-/649/"
TOWN_URL = "https://meguvosbustas.lt/parduodami-namai/Kretinga/2-kambariu-/624/"


def spider(**kwargs) -> MeguvosbustasSpider:
    return make_spider(MeguvosbustasSpider, **kwargs)


def parse_index(sp, markup=None, page=1):
    markup = markup or read_fixture("meguvosbustas_index_p1.html")
    response = make_response(INDEX_URL, markup, index_url=INDEX_URL, page=page)
    return list(sp.parse_index(response))


def detail_item(fixture="meguvosbustas_detail_house.html", url=VILLAGE_URL):
    items = list(spider().parse_detail(make_response(url, read_fixture(fixture))))
    assert len(items) == 1, items
    return items[0]


class StartTest(unittest.TestCase):
    def test_uses_the_shared_base_and_the_registry_url(self) -> None:
        self.assertTrue(issubclass(MeguvosbustasSpider, ListingSpider))
        self.assertEqual([r.url for r in collect_start(spider())], [INDEX_URL])
        self.assertEqual(spider().target, "house")
        self.assertEqual(spider().region, "kretinga")
        self.assertEqual(spider(max_pages="1").max_pages, 1)

    def test_pages_are_path_segments(self) -> None:
        sp = spider()
        self.assertEqual(sp.page_url(INDEX_URL, 1), INDEX_URL)
        self.assertEqual(sp.page_url(INDEX_URL, 3), INDEX_URL + "/puslapis-3")

    def test_no_browser_is_started(self) -> None:
        handlers = MeguvosbustasSpider.custom_settings["DOWNLOAD_HANDLERS"]
        for scheme in ("http", "https"):
            self.assertNotIn("playwright", handlers[scheme].lower())


class UrlTest(unittest.TestCase):
    def test_spaces_go_out_as_plus_not_percent_20(self) -> None:
        # The site's firewall answers 403 to %20 and 200 to +.
        url = site_url(INDEX_URL, "/parduodami-namai/Kretingos r./5-kambariu-/649/")
        self.assertEqual(url, VILLAGE_URL)
        self.assertNotIn("%20", url)

    def test_non_ascii_is_percent_encoded(self) -> None:
        url = site_url(INDEX_URL, "/sklypai/Klaipėdos r./25-aru-/651/")
        self.assertEqual(url, "https://meguvosbustas.lt/sklypai/Klaip%C4%97dos+r./25-aru-/651/")


class IndexTest(unittest.TestCase):
    def test_follows_only_the_house_cards(self) -> None:
        sp = spider()
        details, _ = split_requests(sp, parse_index(sp))
        self.assertEqual(
            sorted(r.url for r in details),
            sorted(
                [
                    VILLAGE_URL,
                    "https://meguvosbustas.lt/parduodami-namai/Kretingos+r./5-kambariu-/621/",
                    TOWN_URL,
                ]
            ),
        )
        # 9 cards on the page: 3 houses followed, 6 plots and flats counted as seen and skipped.
        stats = sp.crawler.stats
        self.assertEqual(stats.get_value("nt/links_seen"), 9)
        self.assertEqual(stats.get_value("nt/links_skipped"), 6)

    def test_next_page_comes_from_the_pager(self) -> None:
        sp = spider()
        _, pages = split_requests(sp, parse_index(sp))
        self.assertEqual([r.url for r in pages], [INDEX_URL + "/puslapis-2"])
        self.assertEqual(pages[0].meta["page"], 2)

    def test_last_page_has_no_next_page(self) -> None:
        markup = read_fixture("meguvosbustas_index_p1.html")
        markup = re.sub(r"<li><a href='[^']*puslapis-2'>2</a></li>", "", markup)
        sp = spider()
        details, pages = split_requests(sp, parse_index(sp, markup))
        self.assertEqual(len(details), 3)
        self.assertEqual(pages, [])

    def test_max_pages_stops_the_walk(self) -> None:
        sp = spider(max_pages="1")
        _, pages = split_requests(sp, parse_index(sp))
        self.assertEqual(pages, [])

    def test_a_house_in_another_district_is_skipped(self) -> None:
        markup = read_fixture("meguvosbustas_index_p1.html").replace(
            "/parduodami-namai/Kretingos r./5-kambariu-/621/",
            "/parduodami-namai/Klaipėdos r./5-kambariu-/621/",
        )
        sp = spider()
        details, _ = split_requests(sp, parse_index(sp, markup))
        self.assertEqual(len(details), 2)
        self.assertNotIn("621", " ".join(r.url for r in details))

    def test_a_page_of_only_other_property_does_not_end_the_walk(self) -> None:
        markup = read_fixture("meguvosbustas_index_p1.html").replace(
            "/parduodami-namai/", "/parduodami-butai/"
        )
        sp = spider()
        details, pages = split_requests(sp, parse_index(sp, markup))
        self.assertEqual(details, [])
        self.assertEqual(len(pages), 1)

    def test_stops_when_a_page_yields_no_new_links(self) -> None:
        sp = spider()
        parse_index(sp)
        self.assertEqual(parse_index(sp, page=2), [])
        self.assertEqual(parse_index(spider(), "<html><body>nothing</body></html>"), [])


class DetailTest(unittest.TestCase):
    def test_village_house_fields(self) -> None:
        item = detail_item()
        self.assertEqual(item["source"], "meguvosbustas")
        self.assertEqual(item["source_id"], "649")
        self.assertEqual(item["url"], VILLAGE_URL)
        self.assertEqual(
            item["title"], "Parduodamas namas Rubulių km., Vynuogių g. 12, Kretingos r."
        )
        self.assertEqual(item["property_type"], "house")
        self.assertEqual(item["listing_type"], "sale")
        self.assertEqual(item["price"], 74000)
        self.assertEqual(item["area_sqm"], 179)
        self.assertEqual(item["plot_area_ares"], 9)
        self.assertEqual(item["rooms"], 5)
        self.assertEqual(item["year_built"], 2019)
        self.assertEqual(item["building_type"], "brick")
        self.assertEqual(item["heating_type"], "Kieto kuro")
        self.assertEqual(item["address"], "Rubulių km., Vynuogių g. 12, Kretingos r. sav.")
        self.assertEqual(item["city"], "Rubulių")
        self.assertEqual(len(item["photo_urls"]), 1)
        self.assertTrue(item["photo_urls"][0].startswith("https://meguvosbustas.lt/images/"))

    def test_town_house_with_price_zero_has_no_price(self) -> None:
        item = detail_item("meguvosbustas_detail_house_town.html", TOWN_URL)
        self.assertEqual(item["source_id"], "624")
        self.assertIsNone(item["price"])
        self.assertEqual(item["address"], "Šiaulių g. 50, Kretinga")
        self.assertEqual(item["city"], "Kretinga")
        self.assertEqual(item["area_sqm"], 73)
        self.assertEqual(item["plot_area_ares"], 8.19)  # a comma decimal ("8,19 a")

    def test_city_is_the_settlement_not_the_district(self) -> None:
        markup = read_fixture("meguvosbustas_detail_house.html")
        village = markup.replace("Rubulių km., Vynuogių g. 12</div>", "Žibalės g., Vydmantai</div>")
        item = list(spider().parse_detail(make_response(VILLAGE_URL, village)))[0]
        self.assertEqual(item["city"], "Vydmantai")
        self.assertEqual(item["address"], "Žibalės g., Vydmantai, Kretingos r. sav.")

        bare = markup.replace("Rubulių km., Vynuogių g. 12</div>", "</div>")
        item = list(spider().parse_detail(make_response(VILLAGE_URL, bare)))[0]
        self.assertEqual(item["city"], "")  # a bare district is not a city
        self.assertEqual(item["address"], "Kretingos r. sav.")

    def test_description_is_the_listings_own_text(self) -> None:
        item = detail_item("meguvosbustas_detail_house_town.html", TOWN_URL)
        self.assertTrue(item["description"].startswith("PARDUODAMA PUSĘ NAMO"))
        self.assertIn("Šildymas dujinis", item["description"])

    def test_the_agencys_standing_ad_text_is_not_the_description(self) -> None:
        for fixture, url in (
            ("meguvosbustas_detail_house.html", VILLAGE_URL),
            ("meguvosbustas_detail_house_town.html", TOWN_URL),
        ):
            text = detail_item(fixture, url)["description"].lower()
            for phrase in ("svarbu", "netaiko jokių mokesčių", "tarpininkavimas", "facebook"):
                self.assertNotIn(phrase, text)

    def test_no_contact_data_reaches_the_item(self) -> None:
        for fixture, url in (
            ("meguvosbustas_detail_house.html", VILLAGE_URL),
            ("meguvosbustas_detail_house_town.html", TOWN_URL),
        ):
            # The fixture really carries the redacted contact box, so this test has teeth.
            self.assertIn("Agentas Pavardenis", read_fixture(fixture))
            blob = dumped(detail_item(fixture, url))
            for value in PLACEHOLDERS:
                self.assertNotIn(value, blob)
            self.assertNotRegex(blob, r"\+370|@|tel\.")

    def test_page_without_a_listing_yields_nothing(self) -> None:
        response = make_response(VILLAGE_URL, "<html><body>403</body></html>")
        self.assertEqual(list(spider().parse_detail(response)), [])

    def test_item_passes_the_listing_pipelines(self) -> None:
        sp = spider()
        item = run_pipelines(sp, detail_item())
        self.assertEqual(item["price"], 74000.0)
        self.assertEqual(item["currency"], "EUR")
        self.assertEqual(item["year_built"], 2019)


if __name__ == "__main__":
    unittest.main()
