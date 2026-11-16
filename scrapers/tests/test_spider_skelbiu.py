"""Skelbiu spider against saved pages (no network).

Run inside the scraper container (stdlib unittest, no pytest in that image):
    docker compose exec scraper_worker python -m unittest discover -s tests

Fixtures in tests/fixtures/ are real skelbiu pages trimmed to the sections the spider reads,
with every contact value replaced (see tests/fixtures/README.md).
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
from realestate_spiders.spiders.listings.base import ListingSpider
from realestate_spiders.spiders.listings.skelbiu import SkelbiuSpider, nominative

INDEX_URL = "https://www.skelbiu.lt/paieska/namai-kretingoje/"
PAGE2_URL = "https://www.skelbiu.lt/paieska/namai-kretingoje/2"
HOUSE_URL = (
    "https://www.skelbiu.lt/skelbimai/"
    "parduodamas-sodo-namas-su-ukiniu-pastatu-kumponuose-elniu-81518753.html"
)
FLAT_URL = (
    "https://www.skelbiu.lt/skelbimai/"
    "parduodamas-erdvus-ir-sviesus-3-kambariu-butas-kretingoje-86968032.html"
)
TIMBER_URL = (
    "https://www.skelbiu.lt/skelbimai/gera-mediena-sausa-reali-kaina-vilnius-kaunas-22612105.html"
)
OUTSIDE_URL = (
    "https://www.skelbiu.lt/skelbimai/namas-prie-juros-ir-misko-gyvenimui-ir-poilsiui-87188154.html"
)


def spider(**kwargs) -> SkelbiuSpider:
    return make_spider(SkelbiuSpider, **kwargs)


def detail_response(fixture="skelbiu_detail_house.html", url=HOUSE_URL, edit=None):
    markup = read_fixture(fixture)
    if edit:
        markup = edit(markup)
    return make_response(url, markup)


def detail_item(fixture="skelbiu_detail_house.html", url=HOUSE_URL, edit=None):
    items = details_of(spider(), detail_response(fixture, url, edit))
    assert len(items) == 1, items
    return items[0]


class StartTest(unittest.TestCase):
    def test_starts_from_the_registry_url(self) -> None:
        requests = collect_start(spider())
        self.assertEqual([r.url for r in requests], [INDEX_URL])
        self.assertFalse(requests[0].meta.get("playwright"))

    def test_args_come_from_the_registry_and_can_be_overridden(self) -> None:
        s = spider()
        self.assertEqual(s.target, "house")
        self.assertEqual(s.max_pages, 10)
        self.assertEqual(s.region, "kretinga")
        self.assertEqual(spider(max_pages="1").max_pages, 1)

    def test_page_is_a_trailing_path_segment_and_page_one_has_none(self) -> None:
        s = spider()
        self.assertEqual(s.page_url(INDEX_URL, 1), INDEX_URL)
        self.assertEqual(s.page_url(INDEX_URL, 2), PAGE2_URL)
        self.assertEqual(s.page_url(INDEX_URL, 10), INDEX_URL + "10")
        # a start URL with no trailing slash, or already on a page, gives the same walk
        self.assertEqual(s.page_url(INDEX_URL.rstrip("/"), 3), INDEX_URL + "3")
        self.assertEqual(s.page_url(PAGE2_URL, 1), INDEX_URL)
        self.assertEqual(s.page_url(PAGE2_URL, 4), INDEX_URL + "4")

    def test_every_request_goes_through_curl_cffi_not_a_browser(self) -> None:
        settings = get_project_settings()
        SkelbiuSpider.update_settings(settings)
        handlers = settings.getdict("DOWNLOAD_HANDLERS")
        for scheme in ("http", "https"):
            self.assertEqual(
                handlers[scheme], "realestate_spiders.handlers.CurlCffiDownloadHandler"
            )
        self.assertNotIn("playwright", (SPIDERS / "skelbiu.py").read_text(encoding="utf-8").lower())

    def test_uses_the_shared_base(self) -> None:
        self.assertTrue(issubclass(SkelbiuSpider, ListingSpider))


class IndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.markup = read_fixture("skelbiu_index_kretinga_p1.html")
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
            self.assertRegex(request.url, r"^https://www\.skelbiu\.lt/skelbimai/[^/]+-\d+\.html$")
        self.assertEqual([r.url for r in pages], [PAGE2_URL])
        self.assertEqual(pages[0].meta["page"], 2)

    def test_counts_links_seen(self) -> None:
        self.parse()
        self.assertEqual(self.spider.crawler.stats.get_value("nt/links_seen"), 24)

    def test_stops_when_a_page_yields_no_new_links(self) -> None:
        self.parse()
        self.assertEqual(self.parse(page=2), [])

    def test_stops_on_an_empty_page(self) -> None:
        self.assertEqual(self.parse("<html><body>Skelbimų nerasta</body></html>"), [])

    def test_max_pages_stops_the_walk(self) -> None:
        s = spider(max_pages="1")
        requests = self.parse(s=s)
        self.assertEqual(len(requests), 24)
        self.assertTrue(all(r.callback == s.parse_detail for r in requests))

    def test_only_result_cards_of_the_listing_shape_are_followed(self) -> None:
        extra = (
            '<a class="standard-list-item" href="/skelbimai/nekilnojamasis-turtas/namai/">x</a>'
            '<a class="standard-list-item" href="/paieska/namai-kretingoje/2">x</a>'
            '<a class="standard-list-item" href="/skelbimai/lizingas.html">x</a>'
            # listing-shaped, but not a result card
            '<a href="/skelbimai/baneris-99999999.html">banner</a>'
        )
        requests = self.parse(self.markup.replace("</body>", extra + "</body>"))
        details = [r.url for r in requests if r.callback == self.spider.parse_detail]
        self.assertEqual(len(details), 24)
        self.assertFalse([u for u in details if "99999999" in u or "lizingas" in u])

    def test_query_and_fragment_are_dropped_from_listing_links(self) -> None:
        card = '<a class="standard-list-item" href="/skelbimai/namas-x-5.html?el=1#top">x</a>'
        requests = self.parse(f"<html><body>{card}</body></html>")
        self.assertEqual(
            [r.url for r in requests if r.callback == self.spider.parse_detail],
            ["https://www.skelbiu.lt/skelbimai/namas-x-5.html"],
        )


class DetailTest(unittest.TestCase):
    def test_fields_come_from_the_page(self) -> None:
        item = detail_item()
        self.assertEqual(item["source"], "skelbiu")
        self.assertEqual(item["source_id"], "81518753")
        self.assertEqual(item["url"], HOUSE_URL)
        self.assertEqual(item["title"], "Parduodamas Sodo Namas Su Ūkiniu Pastatu")
        self.assertEqual(item["price"], 130000.0)
        self.assertEqual(item["area_sqm"], 77.69)
        self.assertEqual(item["plot_area_ares"], 6.34)
        self.assertEqual(item["total_floors"], 2)
        self.assertEqual(item["year_built"], 1982)
        self.assertEqual(item["building_type"], "brick")
        self.assertEqual(item["heating_type"], "Aeroterminis, elektra")
        self.assertEqual(item["property_type"], "house")
        self.assertEqual(item["listing_type"], "sale")
        self.assertEqual(len(item["photo_urls"]), 5)
        self.assertEqual(item["raw_data"]["location"], "Kretingos r., Kumponų vs., Elnių tak. 5")
        self.assertEqual(item["raw_data"]["details"]["pastato tipas"], "Sodo namas")

    def test_address_is_in_the_shape_the_geocoder_reads(self) -> None:
        item = detail_item()
        # municipality with "sav.", settlement in the nominative without "vs.", street, no number
        self.assertEqual(item["address"], "Kretingos r. sav., Kumponai, Elnių tak.")
        self.assertEqual(item["city"], "Kumponai")
        self.assertEqual(item["location_precision"], "street")
        self.assertNotIn("latitude", item)

    def test_description_is_the_listings_text(self) -> None:
        item = detail_item()
        self.assertTrue(item["description"].startswith("PARDUODAMAS SODO NAMAS SU ŪKINIU PASTATU"))
        self.assertNotIn("Paskola būsto įrengimui", item["description"])

    def test_no_contact_data_in_the_item(self) -> None:
        item = detail_item()
        dumped = json.dumps(dict(item), ensure_ascii=False, default=str).lower()
        for key in ("user-card", "profile-data", "contact-call", "/users/signin"):
            self.assertNotIn(key, dumped)
        self.assertNotIn("kavoli", dumped)

    def test_phone_and_email_typed_into_the_description_are_removed_by_the_scrub_pipeline(
        self,
    ) -> None:
        item = detail_item()
        self.assertIn(
            PLACEHOLDER_PHONE, item["description"]
        )  # the spider leaves it to the pipeline
        item = ScrubPipeline().process_item(item, spider())
        self.assertNotIn("600 00000", item["description"])
        self.assertNotIn(PLACEHOLDER_EMAIL, item["description"])

    def test_a_precise_pin_is_used_and_an_approximate_one_flagged(self) -> None:
        def with_pin(approximate):
            return lambda m: re.sub(
                r'data-coordinates="[^"]*"( data-approximate="[^"]*")?',
                f'data-coordinates="55.883,21.2351" data-approximate="{approximate}"',
                m,
            )

        precise = detail_item(edit=with_pin(""))
        self.assertAlmostEqual(precise["latitude"], 55.883)
        self.assertEqual(precise["location_precision"], "street")
        approximate = detail_item(edit=with_pin("1"))
        self.assertAlmostEqual(approximate["longitude"], 21.2351)
        self.assertEqual(approximate["location_precision"], "city")

    def test_a_pin_outside_lithuania_is_ignored(self) -> None:
        item = detail_item(
            edit=lambda m: re.sub(r'data-coordinates="[^"]*"', 'data-coordinates="0,0"', m)
        )
        self.assertNotIn("latitude", item)

    def test_a_house_without_a_street_has_no_street_precision(self) -> None:
        def edit(markup):
            markup = re.sub(r'<div class="details-row"><label>Gatvė:</label>.*?</div>', "", markup)
            return markup

        item = detail_item(edit=edit)
        self.assertEqual(item["address"], "Kretingos r. sav., Kumponai")
        self.assertNotIn("location_precision", item)

    def test_year_keeps_the_build_year_not_the_renovation(self) -> None:
        self.assertEqual(detail_item()["raw_data"]["details"]["metai"], "1982, 2024 renovuotas")
        self.assertEqual(detail_item()["year_built"], 1982)

    def test_item_passes_the_listing_pipelines_without_a_database(self) -> None:
        s = spider()
        item = detail_item()
        for pipeline in (ScrubPipeline(), ValidatePipeline(), NormalizePipeline()):
            item = pipeline.process_item(item, s)
        self.assertEqual(item["price"], 130000.0)
        self.assertEqual(item["year_built"], 1982)
        self.assertEqual(item["currency"], "EUR")


class SkipTest(unittest.TestCase):
    """The search is by keyword, so most hits are not Kretinga houses. Each is judged here."""

    def parse(self, fixture, url, edit=None):
        s = spider()
        return s, details_of(s, detail_response(fixture, url, edit))

    def assert_skipped(self, fixture, url, edit=None) -> None:
        s, items = self.parse(fixture, url, edit)
        self.assertEqual(items, [])
        self.assertEqual(s.crawler.stats.get_value("nt/details_skipped"), 1)

    def test_a_flat_is_skipped(self) -> None:
        self.assert_skipped("skelbiu_detail_flat.html", FLAT_URL)

    def test_a_non_property_ad_is_skipped(self) -> None:
        self.assert_skipped("skelbiu_detail_timber.html", TIMBER_URL)

    def test_a_plot_is_skipped(self) -> None:
        self.assert_skipped(
            "skelbiu_detail_house.html", HOUSE_URL, lambda m: m.replace("/namai/", "/sklypai/")
        )

    def test_a_house_outside_kretinga_district_is_skipped(self) -> None:
        self.assert_skipped("skelbiu_detail_house_outside_area.html", OUTSIDE_URL)

    def test_the_same_house_inside_the_district_is_kept(self) -> None:
        s, items = self.parse(
            "skelbiu_detail_house_outside_area.html",
            OUTSIDE_URL,
            lambda m: m.replace("Klaipėdos r.,", "Kretingos r.,"),
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["address"], "Kretingos r. sav., Bruzdeilynas, Skrydžio g.")

    def test_a_house_priced_per_month_is_skipped(self) -> None:
        def edit(markup):
            assert '<p class="price">130 000 €</p>' in markup
            return markup.replace(
                '<p class="price">130 000 €</p>', '<p class="price">600 €/mėn.</p>'
            )

        self.assert_skipped("skelbiu_detail_house.html", HOUSE_URL, edit)

    def test_a_page_without_a_location_is_skipped(self) -> None:
        self.assert_skipped(
            "skelbiu_detail_house.html",
            HOUSE_URL,
            lambda m: re.sub(r'<span class="cities-name">.*?</span>', "", m),
        )

    def test_a_page_that_is_not_a_listing_yields_nothing(self) -> None:
        s = spider()
        response = make_response(HOUSE_URL, "<html><body>Skelbiu.lt</body></html>")
        self.assertEqual(details_of(s, response), [])
        self.assertIsNone(s.crawler.stats.get_value("nt/details_skipped"))


class NominativeTest(unittest.TestCase):
    def test_village_names_go_from_the_genitive_the_portal_prints_to_the_nominative(self) -> None:
        for printed, expected in {
            "Kumponų vs.": "Kumponai",
            "Kvecių k.": "Kveciai",
            "Kretingsodžio k.": "Kretingsodis",
            "Bruzdeilyno k.": "Bruzdeilynas",
            "Kretingos m.": "Kretinga",
        }.items():
            self.assertEqual(nominative(printed), expected, printed)

    def test_short_names_and_unknown_endings_are_left_alone(self) -> None:
        self.assertEqual(nominative("Ūla"), "Ūla")
        self.assertEqual(nominative("Plungė"), "Plungė")
        self.assertEqual(nominative(""), "")


if __name__ == "__main__":
    unittest.main()
