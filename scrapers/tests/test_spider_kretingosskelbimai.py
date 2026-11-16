"""Kretingos skelbimai spider against saved pages (no network).

    docker compose exec scraper_worker python -m unittest discover -s tests

The board mixes ad types, so most of these tests are about which ads become items.
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

from realestate_spiders.spiders.listings.base import ListingSpider
from realestate_spiders.spiders.listings.kretingosskelbimai import (
    KretingosskelbimaiSpider,
    is_house_for_sale,
)

ROOT = "https://www.kretingosskelbimai.lt"
NAMAI_URL = f"{ROOT}/skelbimai/nekilnojamas-turtas/namai"
CATEGORY_URL = f"{ROOT}/skelbimai/nekilnojamas-turtas"
DETAIL_URL = (
    f"{ROOT}/skelbimas/"
    "259896-parduodamas-jaukiai-skoningai-irengtas-namas-guobu-aklg.-kretingoje.html"
)


def spider(**kwargs) -> KretingosskelbimaiSpider:
    return make_spider(KretingosskelbimaiSpider, **kwargs)


def parse_index(sp, fixture, url, page=1):
    response = make_response(url, read_fixture(fixture), index_url=url, page=page)
    return list(sp.parse_index(response))


def detail_items(mutate=None):
    markup = read_fixture("kretingosskelbimai_detail_house.html")
    if mutate:
        markup = mutate(markup)
    return list(spider().parse_detail(make_response(DETAIL_URL, markup)))


def detail_item(mutate=None):
    items = detail_items(mutate)
    assert len(items) == 1, items
    return items[0]


class StartTest(unittest.TestCase):
    def test_walks_the_houses_and_farmsteads_sub_categories(self) -> None:
        self.assertTrue(issubclass(KretingosskelbimaiSpider, ListingSpider))
        self.assertEqual(
            [r.url for r in collect_start(spider())],
            [NAMAI_URL, f"{ROOT}/skelbimai/nekilnojamas-turtas/sodybos"],
        )
        self.assertEqual(spider().target, "house")

    def test_pages_are_numbered_html_files(self) -> None:
        sp = spider()
        self.assertEqual(sp.page_url(NAMAI_URL, 1), NAMAI_URL)
        self.assertEqual(sp.page_url(NAMAI_URL, 2), NAMAI_URL + "/2.html")

    def test_runs_at_the_registry_delay_without_a_browser(self) -> None:
        self.assertEqual(get_source("kretingosskelbimai").delay_s, 3)
        handlers = KretingosskelbimaiSpider.custom_settings["DOWNLOAD_HANDLERS"]
        for scheme in ("http", "https"):
            self.assertNotIn("playwright", handlers[scheme].lower())


class HouseFilterTest(unittest.TestCase):
    def test_sale_ads_for_houses_pass(self) -> None:
        for title, text in (
            ("Parduodamas namas Kretingoje", ""),
            ("Parduodama sodyba Rubulių k.", "84 kv. m. rąstinė sodyba"),
            ("Parduodama pusė gyvenamojo namo Kretingoje", ""),
            ("Gyvenamas namas", "Parduodamas namas Šukes kaime, galimas keitimas"),
            ("Namas", "Parduodamas namas Kretingoje"),  # the sale word may be in the text only
            ("Parduodamas ERDVUS NAUJOS STATYBOS namas Dupulčiuose", ""),
            ("Parduodamas 2aukštų, 6 kambarių namas , Šilutėje", ""),
        ):
            with self.subTest(title=title):
                self.assertTrue(is_house_for_sale(title, text))

    def test_other_ads_are_filtered_out(self) -> None:
        for title, text in (
            ("Pirktume namą", "Ieškome 4 kambarių namo Kretingoje iki 135tūkst"),
            ("Skubiai energinio naudingumo sertifikatas visoje Lietuvoje", "parduoti namą"),
            ("Grindų betonavimo darbai Kretingoje", "Parduodamas zvyras"),
            ("Fibrocementines fasado dailylentes", "Parduodamos dailylentės namui"),
            ("Išnuomojamas namas Kretingoje", "Parduodamas, ne, nuomojamas"),
            ("Parduodamas sklypas namo statybai", ""),
            ("Parduodamas butas Kretingoje", "Parduodamas 3 kambarių butas"),
        ):
            with self.subTest(title=title):
                self.assertFalse(is_house_for_sale(title, text))

    def test_a_wanted_ad_that_mentions_selling_is_still_wanted(self) -> None:
        self.assertFalse(
            is_house_for_sale("Namas Kretingoje", "Ieškome namo; parduodame savo butą Kretingoje")
        )


class IndexTest(unittest.TestCase):
    def test_follows_only_house_for_sale_ads_of_the_mixed_category(self) -> None:
        sp = spider()
        details, pages = split_requests(
            sp, parse_index(sp, "kretingosskelbimai_index_namai.html", NAMAI_URL)
        )
        followed = sorted(r.url.rsplit("/", 1)[1].split("-", 1)[0] for r in details)
        # 12 ads: the energy certificate, the concrete work and "we want a house" are skipped.
        self.assertEqual(
            followed,
            sorted(
                [
                    "259639",
                    "259896",
                    "221115",
                    "268528",
                    "268215",
                    "267695",
                    "267667",
                    "259796",
                    "258673",
                ]
            ),
        )
        stats = sp.crawler.stats
        self.assertEqual(stats.get_value("nt/links_seen"), 12)
        self.assertEqual(stats.get_value("nt/links_skipped"), 3)
        self.assertEqual(pages, [])  # a sub-category of one page has no pager

    def test_detail_urls_are_absolute(self) -> None:
        sp = spider()
        details, _ = split_requests(
            sp, parse_index(sp, "kretingosskelbimai_index_namai.html", NAMAI_URL)
        )
        for request in details:
            self.assertRegex(request.url, rf"^{ROOT}/skelbimas/\d+-.+\.html$")

    def test_a_whole_category_page_of_other_ads_walks_on_to_the_next_page(self) -> None:
        sp = spider()
        fixture = "kretingosskelbimai_index_category_p1.html"
        details, pages = split_requests(sp, parse_index(sp, fixture, CATEGORY_URL))
        self.assertEqual(details, [])  # building materials only
        self.assertEqual([r.url for r in pages], [f"{CATEGORY_URL}/2.html"])
        self.assertEqual(sp.crawler.stats.get_value("nt/links_skipped"), 4)

    def test_the_last_page_has_no_successor(self) -> None:
        # The saved pager links 2 to 9; asked as page 9 it has no "10.html" to follow.
        sp = spider()
        fixture = "kretingosskelbimai_index_category_p1.html"
        _, pages = split_requests(sp, parse_index(sp, fixture, CATEGORY_URL, page=9))
        self.assertEqual(pages, [])

    def test_max_pages_stops_the_walk(self) -> None:
        sp = spider(max_pages="1")
        fixture = "kretingosskelbimai_index_category_p1.html"
        self.assertEqual(split_requests(sp, parse_index(sp, fixture, CATEGORY_URL))[1], [])


class DetailTest(unittest.TestCase):
    def test_fields(self) -> None:
        item = detail_item()
        self.assertEqual(item["source"], "kretingosskelbimai")
        self.assertEqual(item["source_id"], "259896")
        self.assertEqual(item["url"], DETAIL_URL)
        self.assertEqual(
            item["title"], "Parduodamas JAUKIAI, SKONINGAI įrengtas namas Guobų aklg., Kretingoje!"
        )
        self.assertEqual(item["property_type"], "house")
        self.assertEqual(item["listing_type"], "sale")
        self.assertEqual(item["price"], 365000)
        self.assertEqual(item["area_sqm"], 231.23)
        self.assertEqual(item["plot_area_ares"], 8.71)
        self.assertEqual(item["year_built"], 2007)
        self.assertEqual(item["raw_data"]["location_tag"], "Kretinga")

    def test_address_is_the_locative_place_from_the_title_and_city_is_nominative(self) -> None:
        item = detail_item()
        self.assertEqual(item["address"], "Guobų aklg., Kretingoje")
        self.assertEqual(item["city"], "Kretinga")

    def test_without_a_place_in_the_text_the_posters_location_tag_is_the_address(self) -> None:
        def mutate(markup):
            return markup.replace(
                "Parduodamas JAUKIAI, SKONINGAI įrengtas namas Guobų aklg., Kretingoje!",
                "Parduodamas ERDVUS namas LABAI GEROJE VIETOJE!",
            ).replace("Guobų aklg., Kretingoje, parduodamas", "Parduodamas")

        item = detail_item(mutate)
        self.assertEqual(item["address"], "Kretinga")

    def test_a_house_posted_from_kretinga_but_in_another_town_uses_the_town(self) -> None:
        def mutate(markup):
            return markup.replace("Guobų aklg., Kretingoje!", "Jovarų g., Šilutėje!").replace(
                "Guobų aklg., Kretingoje, parduodamas", "Šilutėje parduodamas"
            )

        item = detail_item(mutate)
        self.assertEqual(item["address"], "Jovarų g., Šilutėje")
        self.assertEqual(item["city"], "Šilutė")

    def test_photos_are_the_large_versions_of_the_gallery(self) -> None:
        photos = detail_item()["photo_urls"]
        self.assertEqual(len(photos), 10)
        for url in photos:
            self.assertRegex(url, rf"^{ROOT}/foto/l-parduodamas-jaukiai-.+\.webp$")

    def test_description_is_the_text_without_the_signature_block(self) -> None:
        text = detail_item()["description"]
        self.assertTrue(text.startswith("Guobų aklg., Kretingoje, parduodamas JAUKUS"))
        self.assertIn("Įrengta 11 kW elektromobilių įkrovimo stotelė!", text)
        self.assertTrue(text.endswith("netaikomi agentūros mokesčiai!"))
        for part in ("Nekilnojamojo turto ekspert", "El.p.", "Mob.", "kreipkitės", "www.", "____"):
            self.assertNotIn(part, text)

    def test_the_price_box_is_not_part_of_the_description(self) -> None:
        self.assertNotIn("Kaina", detail_item()["description"])

    def test_no_contact_data_reaches_the_item(self) -> None:
        # The fixture really carries the redacted signature and contact box (so this has teeth).
        markup = read_fixture("kretingosskelbimai_detail_house.html")
        self.assertIn("Agentas Pavardenis", markup)
        self.assertIn("sk_contacts", markup)
        blob = dumped(detail_item())
        for value in PLACEHOLDERS:
            self.assertNotIn(value, blob)
        self.assertNotIn("@", blob)

    def test_a_non_house_ad_is_not_an_item(self) -> None:
        def mutate(markup):
            return markup.replace(
                "Parduodamas JAUKIAI, SKONINGAI įrengtas namas Guobų aklg., Kretingoje!",
                "Pirktume namą",
            ).replace("parduodamas JAUKUS", "ieškome")

        self.assertEqual(detail_items(mutate), [])

    def test_page_without_a_listing_yields_nothing(self) -> None:
        response = make_response(DETAIL_URL, "<html><body>Skelbimas nerastas</body></html>")
        self.assertEqual(list(spider().parse_detail(response)), [])

    def test_item_passes_the_listing_pipelines(self) -> None:
        item = run_pipelines(spider(), detail_item())
        self.assertEqual(item["price"], 365000.0)
        self.assertEqual(item["currency"], "EUR")


if __name__ == "__main__":
    unittest.main()
