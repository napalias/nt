"""Realu spider against saved pages (no network).

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
from urllib.parse import parse_qs

from scrapy import Request
from scrapy.http import FormRequest, HtmlResponse, TextResponse
from scrapy.settings.default_settings import DOWNLOAD_HANDLERS_BASE
from scrapy.utils.project import get_project_settings
from scrapy.utils.test import get_crawler

from realestate_spiders.pipelines import NormalizePipeline, ScrubPipeline, ValidatePipeline
from realestate_spiders.spiders.listings.realu import RealuSpider

FIXTURES = Path(__file__).parent / "fixtures"
SEARCH_URL = "https://www.realu.lt/nekilnojamasis-turtas/pirkti/namas/kretingos-r-sav"
ENDPOINT = "https://www.realu.lt/index.php?route=product/category/getEstates"
DETAIL_URL = (
    "https://www.realu.lt/parduodamas-namas-jokubavo-k-kretingos-r-sav-66-m2-ploto-2-aukstai-"
    "realu113233"
)
PLOT_URL = (
    "https://www.realu.lt/parduodamas-namas-mokyklos-g-rudaiciu-k-kretingos-r-sav-277-m2-ploto-"
    "2-aukstai-realu160471"
)
# Contact values the fixtures carry in place of the real ones; none may reach an item.
FIXTURE_CONTACT_VALUES = ("Agentas Pavardenis", "+370 600 00000", "37060000000", "example.invalid")


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def html_response(url: str, markup: str, **meta) -> HtmlResponse:
    return HtmlResponse(
        url, body=markup.encode("utf-8"), encoding="utf-8", request=Request(url, meta=meta)
    )


def estates_response(payload: str | dict, page: int = 1) -> TextResponse:
    body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    request = Request(ENDPOINT, meta={"index_url": SEARCH_URL, "page": page})
    return TextResponse(ENDPOINT, body=body.encode("utf-8"), encoding="utf-8", request=request)


def make_spider(**kwargs) -> RealuSpider:
    return get_crawler(RealuSpider)._create_spider(**kwargs)


def armed_spider(**kwargs) -> RealuSpider:
    """A spider that has read the saved search page, as after its first request."""
    spider = make_spider(**kwargs)
    list(
        spider.parse_search_page(
            html_response(SEARCH_URL, read_fixture("realu_search_page.html"), index_url=SEARCH_URL)
        )
    )
    return spider


def detail_item(name="realu_detail_house.html", url=DETAIL_URL, mutate=None):
    markup = read_fixture(name)
    if mutate:
        markup = mutate(markup)
    items = list(make_spider().parse_detail(html_response(url, markup)))
    assert len(items) == 1, items
    return items[0]


def body_of(request: Request) -> dict[str, str]:
    return {
        k: v[0] for k, v in parse_qs(request.body.decode("utf-8"), keep_blank_values=True).items()
    }


class StartTest(unittest.TestCase):
    def test_first_request_is_the_registry_page_not_a_browser_page(self) -> None:
        spider = make_spider()

        async def run():
            return [r async for r in spider.start()]

        requests = asyncio.run(run())
        self.assertEqual([r.url for r in requests], [SEARCH_URL])
        self.assertFalse(requests[0].meta.get("playwright"))
        self.assertEqual(requests[0].callback, spider.parse_search_page)

    def test_args_come_from_the_registry_and_can_be_overridden(self) -> None:
        spider = make_spider()
        self.assertEqual(spider.target, "house")
        self.assertEqual(spider.max_pages, 10)
        self.assertEqual(spider.region, "kretinga")
        self.assertEqual(make_spider(max_pages="1").max_pages, 1)

    def test_search_page_form_becomes_the_first_post(self) -> None:
        spider = make_spider()
        response = html_response(
            SEARCH_URL, read_fixture("realu_search_page.html"), index_url=SEARCH_URL
        )
        (request,) = list(spider.parse_search_page(response))
        self.assertIsInstance(request, FormRequest)
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url, ENDPOINT)
        self.assertEqual(request.callback, spider.parse_index)
        self.assertEqual(request.meta["page"], 1)
        self.assertEqual(request.headers["X-Requested-With"], b"XMLHttpRequest")
        form = body_of(request)
        self.assertEqual(form["op"], "sale")
        self.assertEqual(form["estate_type"], "house")
        self.assertEqual(form["mls_municipality_id"], "127")
        self.assertEqual(form["municipality_slug"], "kretingos-r-sav")
        self.assertEqual(form["country_id"], "123")
        self.assertEqual(form["page"], "1")

    def test_a_search_page_that_is_not_houses_for_sale_is_not_crawled(self) -> None:
        markup = read_fixture("realu_search_page.html").replace(
            'value="house" data-slug="namas"  selected="selected"',
            'value="flat" data-slug="butas"  selected="selected"',
        )
        spider = make_spider()
        response = html_response(SEARCH_URL, markup, index_url=SEARCH_URL)
        self.assertEqual(list(spider.parse_search_page(response)), [])

    def test_a_page_without_the_search_form_is_not_crawled(self) -> None:
        spider = make_spider()
        response = html_response(SEARCH_URL, "<html><body>nothing</body></html>")
        response.request.meta["index_url"] = SEARCH_URL
        self.assertEqual(list(spider.parse_search_page(response)), [])

    def test_playwright_download_handler_is_off_for_this_spider(self) -> None:
        settings = get_project_settings()
        RealuSpider.update_settings(settings)
        handlers = settings.getdict("DOWNLOAD_HANDLERS")
        for scheme in ("http", "https"):
            self.assertNotIn("playwright", handlers[scheme].lower())
            self.assertEqual(handlers[scheme], DOWNLOAD_HANDLERS_BASE[scheme])

    def test_spider_has_no_playwright_in_it(self) -> None:
        source = (
            Path(__file__).parent.parent
            / "realestate_spiders"
            / "spiders"
            / "listings"
            / "realu.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("playwright", source.lower().replace("no browser", ""))
        self.assertNotIn("127", source)  # the municipality id comes from the page's form


class IndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = json.loads(read_fixture("realu_estates_p1.json"))
        self.spider = armed_spider()

    def parse(self, payload=None, page=1, spider=None):
        response = estates_response(payload or self.payload, page)
        return list((spider or self.spider).parse_index(response))

    def test_one_detail_request_per_card_and_a_next_page_post(self) -> None:
        requests = self.parse()
        details = [r for r in requests if r.callback == self.spider.parse_detail]
        pages = [r for r in requests if r.callback == self.spider.parse_index]
        self.assertEqual(len(details), 10)
        self.assertEqual(len({r.url for r in details}), 10)
        for request in details:
            self.assertRegex(request.url, r"^https://www\.realu\.lt/parduodamas-namas-.+-realu\d+$")
            self.assertFalse(request.meta.get("playwright"))
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].method, "POST")
        self.assertEqual(pages[0].url, ENDPOINT)
        self.assertEqual(pages[0].meta["page"], 2)
        form = body_of(pages[0])
        self.assertEqual(form["page"], "2")
        self.assertEqual(form["mls_municipality_id"], "127")
        self.assertEqual(form["op"], "sale")

    def test_counts_links_seen(self) -> None:
        self.parse()
        self.assertEqual(self.spider.crawler.stats.get_value("nt/links_seen"), 10)

    def test_stops_when_a_page_yields_no_new_links(self) -> None:
        self.parse()
        self.assertEqual(self.parse(page=2), [])  # past the last page: the same cards again

    def test_stops_on_an_empty_page(self) -> None:
        self.assertEqual(self.parse({**self.payload, "view": ""}), [])

    def test_stops_on_a_reply_that_is_not_json(self) -> None:
        self.assertEqual(self.parse("<html>Just a moment...</html>"), [])

    def test_max_pages_stops_the_walk(self) -> None:
        spider = armed_spider(max_pages="1")
        requests = self.parse(spider=spider)
        self.assertEqual(len([r for r in requests if r.callback == spider.parse_detail]), 10)
        self.assertEqual([r for r in requests if r.callback == spider.parse_index], [])

    def test_cards_that_are_not_houses_for_sale_are_seen_but_not_followed(self) -> None:
        view = self.payload["view"]
        view = view.replace("                    namas\n", "                    butas\n", 1)
        rental = 'href="/nuomojamas-namas-kretingos-m-'
        view = view.replace('href="/parduodamas-namas-kretingos-m-', rental, 1)
        requests = self.parse({**self.payload, "view": view})
        details = [r for r in requests if r.callback == self.spider.parse_detail]
        self.assertEqual(len(details), 8)  # one card is a flat, another a rental
        self.assertEqual(self.spider.crawler.stats.get_value("nt/links_seen"), 10)
        self.assertEqual(self.spider.crawler.stats.get_value("nt/links_skipped"), 2)
        for request in details:
            self.assertNotIn("nuomojamas", request.url)


class DetailTest(unittest.TestCase):
    def test_fields_from_the_listing_page(self) -> None:
        item = detail_item()
        self.assertEqual(item["source"], "realu")
        self.assertEqual(item["source_id"], "113233")
        self.assertEqual(item["url"], DETAIL_URL)
        self.assertEqual(
            item["title"],
            "Parduodamas namas Jokūbavo k., Kretingos r. sav., 66 m2 ploto, 2 aukštai",
        )
        self.assertEqual(item["price"], "75000")
        self.assertEqual(item["area_sqm"], 66.0)
        self.assertEqual(item["rooms"], 2)
        self.assertEqual(item["total_floors"], 2)
        self.assertEqual(item["year_built"], "2010")
        self.assertEqual(item["heating_type"], "Kietu kuru")
        self.assertEqual(item["address"], "Jokūbavo k., Kretingos r. sav.")
        self.assertEqual(item["property_type"], "house")
        self.assertEqual(item["listing_type"], "sale")
        self.assertIsNone(item.get("plot_area_ares"))  # this ad gives it only in the description

    def test_every_item_has_a_source_url(self) -> None:
        for name, url in (
            ("realu_detail_house.html", DETAIL_URL),
            ("realu_detail_plot.html", PLOT_URL),
        ):
            with self.subTest(url=url):
                self.assertEqual(detail_item(name, url)["url"], url)

    def test_plot_building_type_and_rooms(self) -> None:
        item = detail_item("realu_detail_plot.html", PLOT_URL)
        self.assertEqual(item["source_id"], "160471")
        self.assertEqual(item["price"], "330000")
        self.assertEqual(item["area_sqm"], 277.0)
        self.assertEqual(item["plot_area_ares"], 15.0)
        self.assertEqual(item["rooms"], 7)
        self.assertEqual(item["building_type"], "brick")
        self.assertEqual(item["address"], "Mokyklos g., Rūdaičių k., Kretingos r. sav.")

    def test_address_when_the_title_writes_the_size_with_a_superscript_and_no_comma(self) -> None:
        def retitle(markup):
            return markup.replace(
                "Kretingos r. sav., 66 m2 ploto, 2 aukštai",
                "Kretingos r. sav., 64.36 m² ploto 3 kambariai",
            )

        item = detail_item(mutate=retitle)
        self.assertEqual(item["address"], "Jokūbavo k., Kretingos r. sav.")

    def test_description_joins_the_wrapped_lines(self) -> None:
        item = detail_item()
        self.assertTrue(item["description"].startswith("Kretingos r.,Rasa s/b parduodamas"))
        self.assertIn("su namu.Mūrinis renovuotas namas", item["description"])
        self.assertNotIn("\n", item["description"])

    def test_photos_are_this_listings_own_full_size_images(self) -> None:
        item = detail_item()
        self.assertEqual(len(item["photo_urls"]), 5)
        self.assertEqual(len(set(item["photo_urls"])), 5)
        for url in item["photo_urls"]:
            self.assertIn("/estate_113233/", url)
            self.assertTrue(url.endswith("-cw.jpg"))
        # nothing from the "similar ads" strip
        self.assertFalse(any("999999" in url for url in item["photo_urls"]))

    def test_no_contact_data_in_the_item(self) -> None:
        item = detail_item()
        dumped = json.dumps(dict(item), ensure_ascii=False, default=str)
        for value in FIXTURE_CONTACT_VALUES:
            self.assertNotIn(value, dumped)
        for key in ("tel:", "mailto:", "broker", "phone", "email"):
            self.assertNotIn(key, dumped.lower())

    def test_query_string_is_not_kept_in_the_url(self) -> None:
        item = detail_item(url=DETAIL_URL + "?open_gallery=1")
        self.assertEqual(item["url"], DETAIL_URL)

    def test_a_rental_page_yields_nothing(self) -> None:
        def rent(markup):
            return markup.replace("<span>Pardavimas</span>", "<span>Nuoma</span>")

        response = html_response(DETAIL_URL, rent(read_fixture("realu_detail_house.html")))
        self.assertEqual(list(make_spider().parse_detail(response)), [])

    def test_missing_optional_blocks_do_not_crash(self) -> None:
        def strip(markup):
            return markup.replace('class="dd"', 'class="x"').replace("accordion-body", "nothing")

        item = detail_item(mutate=strip)
        self.assertEqual(item["source_id"], "113233")
        self.assertIsNone(item.get("area_sqm"))

    def test_page_that_is_not_a_listing_yields_nothing(self) -> None:
        response = html_response(DETAIL_URL, "<html><body>Just a moment...</body></html>")
        self.assertEqual(list(make_spider().parse_detail(response)), [])

    def test_item_passes_the_listing_pipelines_without_a_database(self) -> None:
        def with_phone(markup):
            return markup.replace(
                "automobiliams pastatyti.", "automobiliams pastatyti. Tel. +370 600 12345."
            )

        spider = make_spider()
        item = detail_item(mutate=with_phone)
        for pipeline in (ScrubPipeline(), ValidatePipeline(), NormalizePipeline()):
            item = pipeline.process_item(item, spider)
        self.assertEqual(item["price"], 75000.0)
        self.assertEqual(item["area_sqm"], 66.0)
        self.assertEqual(item["rooms"], 2)
        self.assertNotIn("12345", item["description"])


if __name__ == "__main__":
    unittest.main()
