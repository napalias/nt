"""Jev's ingest screens in the scrapers (#18): the house-ad and new-build pipeline, the wall
question in the block middleware, and the mixed-feed spiders handing their rejects to the
pipeline while a mode is switched on.

Run inside the scraper container (stdlib only):
    docker compose exec scraper_worker python -m unittest discover -s tests

No network, no key, no database: Jev is a fake client and the judgment log writer is replaced
(this container's database is the dev one). Every contact value is a placeholder.
"""

import unittest
from unittest.mock import patch

from apps.classifier import judge
from apps.listings.blocks import BlockKind, stat_key
from django.test import override_settings
from scrapy import Request
from scrapy.exceptions import DropItem
from scrapy.http import HtmlResponse
from scrapy.utils.test import get_crawler
from spider_helpers import (
    details_of,
    make_response,
    make_spider,
    read_fixture,
)

from realestate_spiders.items import ListingItem
from realestate_spiders.middlewares import MAX_WALL_CHECKS, BlockDetectionMiddleware
from realestate_spiders.pipelines import JevScreenPipeline
from realestate_spiders.spiders.listings.alio import AlioSpider
from realestate_spiders.spiders.listings.kretingosskelbimai import KretingosskelbimaiSpider
from realestate_spiders.spiders.listings.skelbiu import SkelbiuSpider

HOUSE = "is_house_sale_ad"
NEW_BUILD = "is_new_build"
WALL = "wall_kind"


class FakeJev:
    """Answers yes/no questions with a fixed P(yes) and the wall choice with a fixed option."""

    def __init__(self, yes=0.5, wall="none"):
        self.yes, self.wall, self.calls = yes, wall, []

    def system_one(self, *, state, questions, model, timeout):
        self.calls.append({"state": state, "questions": list(questions)})
        answers = {}
        for name, body in questions.items():
            if body["type"] == "noul":
                p = self.yes[name] if isinstance(self.yes, dict) else self.yes
                answers[name] = {"type": "noul", "noul": p}
            else:
                options = list(body["criteria"])
                probabilities = {o: (0.97 if o == self.wall else 0.01) for o in options}
                answers[name] = {
                    "type": "choice",
                    "choice": self.wall,
                    "probabilities": probabilities,
                }
        return {"model": model, "answers": answers, "usage": {}}


class JevCase(unittest.TestCase):
    """Base: a JEV_MODE, a fake Jev, and no writes to the dev database."""

    def use(self, mode, jev=None):
        override = override_settings(
            JEV_MODE=mode,
            JEV_MODEL="jev-1.14.0",
            JEV_DEFAULT_THRESHOLD={"yes": 0.90, "no": 0.10, "min_probability": 0.90},
            JEV_THRESHOLDS={},
        )
        override.enable()
        self.addCleanup(override.disable)
        self.jev = jev or FakeJev()
        for target, new in (
            (judge, {"get_default_client": lambda: self.jev}),
            (judge, {"_write_logs": self.record_logs}),
        ):
            patcher = patch.multiple(target, **new)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.logged = []

    def record_logs(self, **kwargs):
        self.logged.append(kwargs)


def listing(**overrides):
    item = ListingItem(
        url="https://www.skelbiu.lt/skelbimai/namas-1.html",
        source="skelbiu",
        source_id="1",
        title="Parduodamas namas Kretingoje",
        description="Namas 100 kv.m. Skambinkite +370 600 00000",
        address="Kretinga",
        city="Kretinga",
        property_type="house",
        listing_type="sale",
        price="200 000",
        year_built="1990",
        raw_data={},
    )
    for key, value in overrides.items():
        item[key] = value
    return item


def pipeline_spider(name="skelbiu"):
    spider = type("S", (), {})()
    spider.name = name
    spider.crawler = get_crawler()
    spider.crawler.stats.open_spider()
    return spider


class PipelineOffTest(JevCase):
    def setUp(self):
        self.use("off")

    def test_changes_nothing_and_never_calls_jev(self):
        item = listing(_house_ad_heuristic=True)
        out = JevScreenPipeline().process_item(item, pipeline_spider())
        self.assertNotIn("_house_ad_heuristic", out)  # the marker never moves on
        self.assertFalse(out.get("is_new_construction"))
        self.assertEqual(self.jev.calls, [])

    def test_an_ad_the_code_filter_rejected_is_dropped_and_counted(self):
        spider = pipeline_spider()
        with self.assertRaises(DropItem) as ctx:
            JevScreenPipeline().process_item(listing(_house_ad_heuristic=False), spider)
        self.assertEqual(str(ctx.exception), "Not a house for sale (keyword filter)")
        self.assertEqual(spider.crawler.stats.get_value("nt/items_screened"), 1)

    def test_another_pipeline_item_type_is_left_alone(self):
        self.assertEqual(JevScreenPipeline().process_item({"x": 1}, pipeline_spider()), {"x": 1})


class PipelineShadowTest(JevCase):
    def test_jev_is_logged_and_the_item_is_unchanged(self):
        self.use("shadow", FakeJev(yes=0.97))
        item = listing(_house_ad_heuristic=True)
        out = JevScreenPipeline().process_item(item, pipeline_spider())
        self.assertFalse(out.get("is_new_construction"))  # Jev says new; shadow does not apply it
        self.assertEqual(sorted(self.jev.calls[0]["questions"]), sorted([HOUSE, NEW_BUILD]))
        self.assertEqual(len(self.logged), 1)
        self.assertEqual(self.logged[0]["kind"], "ingest")
        self.assertNotIn("600 00000", str(self.jev.calls[0]["state"]))

    def test_the_code_filters_no_still_drops_in_shadow(self):
        self.use("shadow", FakeJev(yes=0.97))
        with self.assertRaises(DropItem) as ctx:
            JevScreenPipeline().process_item(listing(_house_ad_heuristic=False), pipeline_spider())
        self.assertIn("keyword filter", str(ctx.exception))

    def test_an_unmixed_feed_is_asked_only_about_new_build(self):
        self.use("shadow")
        item = listing(source="domoplius")
        JevScreenPipeline().process_item(item, pipeline_spider("domoplius"))
        self.assertEqual(self.jev.calls[0]["questions"], [NEW_BUILD])


class PipelineOnTest(JevCase):
    def test_jev_drops_a_non_house(self):
        self.use("on", FakeJev(yes={HOUSE: 0.02, NEW_BUILD: 0.5}))
        spider = pipeline_spider()
        with self.assertRaises(DropItem) as ctx:
            JevScreenPipeline().process_item(listing(_house_ad_heuristic=True), spider)
        self.assertEqual(str(ctx.exception), "Not a house for sale (Jev)")
        self.assertEqual(spider.crawler.stats.get_value("nt/items_screened"), 1)

    def test_jev_rescues_what_the_filter_rejected(self):
        self.use("on", FakeJev(yes={HOUSE: 0.97, NEW_BUILD: 0.5}))
        out = JevScreenPipeline().process_item(
            listing(_house_ad_heuristic=False), pipeline_spider()
        )
        self.assertEqual(out["title"], "Parduodamas namas Kretingoje")

    def test_new_build_is_set_from_jev(self):
        self.use("on", FakeJev(yes={HOUSE: 0.97, NEW_BUILD: 0.97}))
        out = JevScreenPipeline().process_item(listing(_house_ad_heuristic=True), pipeline_spider())
        self.assertIs(out["is_new_construction"], True)

    def test_unsure_jev_falls_back_to_the_keyword_list(self):
        self.use("on", FakeJev(yes=0.5))
        out = JevScreenPipeline().process_item(
            listing(title="Naujos statybos namas", year_built="2025", _house_ad_heuristic=True),
            pipeline_spider(),
        )
        self.assertIs(out["is_new_construction"], True)

    def test_a_missing_key_falls_back_and_the_crawl_goes_on(self):
        self.use("on")
        with patch.object(judge, "get_default_client", side_effect=judge.JevUnavailableError("x")):
            out = JevScreenPipeline().process_item(
                listing(_house_ad_heuristic=True), pipeline_spider()
            )
        self.assertEqual(out["source_id"], "1")


class MixedFeedRegistryTest(unittest.TestCase):
    def test_the_registry_decides_which_sources_are_mixed(self):
        pipeline = JevScreenPipeline()
        for name, expected in {
            "skelbiu": True,
            "alio": True,
            "kretingosskelbimai": True,
            "domoplius": False,
            "not-a-source": False,
        }.items():
            with self.subTest(name):
                self.assertEqual(pipeline._mixed_feed(pipeline_spider(name)), expected)


class SpiderHandoverTest(JevCase):
    """With a mode on, a mixed-feed spider hands its code filter's rejects to the pipeline."""

    def skelbiu_timber(self):
        spider = make_spider(SkelbiuSpider)
        response = make_response(
            "https://www.skelbiu.lt/skelbimai/gera-mediena-sausa-reali-kaina-vilnius-kaunas-"
            "22612105.html",
            read_fixture("skelbiu_detail_timber.html"),
        )
        return spider, details_of(spider, response)

    def test_off_skips_the_page_as_before(self):
        self.use("off")
        spider, items = self.skelbiu_timber()
        self.assertEqual(items, [])
        self.assertEqual(spider.crawler.stats.get_value("nt/details_skipped"), 1)

    def test_shadow_keeps_it_flagged_for_the_pipeline_when_it_is_in_the_area(self):
        self.use("shadow")
        spider = make_spider(SkelbiuSpider)
        markup = read_fixture("skelbiu_detail_house.html").replace("/namai/", "/sklypai/")
        url = (
            "https://www.skelbiu.lt/skelbimai/"
            "parduodamas-sodo-namas-su-ukiniu-pastatu-kumponuose-elniu-81518753.html"
        )
        items = details_of(spider, make_response(url, markup))
        self.assertEqual(len(items), 1)
        self.assertIs(items[0]["_house_ad_heuristic"], False)
        self.assertIsNone(spider.crawler.stats.get_value("nt/details_skipped"))

    def test_the_scan_area_stays_a_hard_skip_in_every_mode(self):
        self.use("on")
        spider, items = self.skelbiu_timber()  # a Vilnius ad: out of area as well
        self.assertEqual(items, [])
        self.assertEqual(spider.crawler.stats.get_value("nt/details_skipped"), 1)

    def test_a_house_the_filter_accepts_is_flagged_true(self):
        self.use("off")
        spider = make_spider(SkelbiuSpider)
        url = (
            "https://www.skelbiu.lt/skelbimai/"
            "parduodamas-sodo-namas-su-ukiniu-pastatu-kumponuose-elniu-81518753.html"
        )
        items = details_of(spider, make_response(url, read_fixture("skelbiu_detail_house.html")))
        self.assertIs(items[0]["_house_ad_heuristic"], True)

    def test_alio_rental_in_shadow_reaches_the_pipeline_and_is_dropped_there(self):
        self.use("shadow", FakeJev(yes=0.5))
        spider = make_spider(AlioSpider)
        url = "https://www.alio.lt/skelbimai/sodo-namas-kretingos-r.-sav.-daubenai/ID69851715.html"
        markup = read_fixture("alio_detail_house.html").replace(
            '"name": "Parduoda"', '"name": "Nuomoja"'
        )
        items = details_of(spider, make_response(url, markup))
        self.assertEqual(len(items), 1)
        self.assertIs(items[0]["_house_ad_heuristic"], False)
        with self.assertRaises(DropItem):
            JevScreenPipeline().process_item(items[0], spider)

    def test_kretingosskelbimai_wanted_ad_is_kept_in_shadow_skipped_in_off(self):
        url = (
            "https://www.kretingosskelbimai.lt/skelbimas/"
            "259896-parduodamas-jaukiai-skoningai-irengtas-namas-guobu-aklg.-kretingoje.html"
        )
        markup = (
            read_fixture("kretingosskelbimai_detail_house.html")
            .replace(
                "Parduodamas JAUKIAI, SKONINGAI įrengtas namas Guobų aklg., Kretingoje!",
                "Pirktume namą",
            )
            .replace("parduodamas JAUKUS", "ieškome")
        )
        for mode, expected in (("off", 0), ("shadow", 1)):
            with self.subTest(mode):
                self.use(mode)
                spider = make_spider(KretingosskelbimaiSpider)
                items = list(spider.parse_detail(make_response(url, markup)))
                self.assertEqual(len(items), expected)
                if items:
                    self.assertIs(items[0]["_house_ad_heuristic"], False)


SHORT_PAGE = "<html><head><title>Palaukite</title></head><body>Tikriname...</body></html>"
LONG_PAGE = "<html><body>" + "<p>Parduodamas namas Kretingoje.</p>" * 60 + "</body></html>"
URL = "https://www.skelbiu.lt/paieska/namai-kretingoje/"


def _middleware():
    crawler = get_crawler()
    crawler.stats.open_spider()
    return BlockDetectionMiddleware.from_crawler(crawler), crawler.stats


def _html(body, status=200, url=URL):
    return HtmlResponse(
        url, status=status, body=body, encoding="utf-8", request=Request(url, meta={})
    )


class WallKindTest(JevCase):
    def test_off_never_asks(self):
        self.use("off")
        mw, stats = _middleware()
        response = _html(SHORT_PAGE)
        mw.process_response(response.request, response)
        self.assertEqual(self.jev.calls, [])
        self.assertIsNone(stats.get_value("nt/wall_judged"))

    def test_shadow_logs_but_records_no_block(self):
        self.use("shadow", FakeJev(wall="challenge"))
        mw, stats = _middleware()
        response = _html(SHORT_PAGE)
        self.assertIs(mw.process_response(response.request, response), response)
        self.assertEqual(self.logged[0]["kind"], "wall")
        self.assertEqual(stats.get_value("nt/wall_judged"), 1)
        self.assertIsNone(stats.get_value(stat_key(BlockKind.CHALLENGE)))

    def test_on_records_the_kind_jev_decides(self):
        self.use("on", FakeJev(wall="login"))
        mw, stats = _middleware()
        response = _html(SHORT_PAGE)
        self.assertIs(mw.process_response(response.request, response), response)
        self.assertEqual(stats.get_value(stat_key(BlockKind.LOGIN)), 1)

    def test_on_none_records_nothing(self):
        self.use("on", FakeJev(wall="none"))
        mw, stats = _middleware()
        response = _html(SHORT_PAGE)
        mw.process_response(response.request, response)
        self.assertFalse([k for k in stats.get_stats() if k.startswith("nt/block/")])

    def test_a_page_with_plenty_of_text_is_not_asked(self):
        self.use("on", FakeJev(wall="captcha"))
        mw, _ = _middleware()
        response = _html(LONG_PAGE)
        mw.process_response(response.request, response)
        self.assertEqual(self.jev.calls, [])

    def test_a_page_the_code_markers_recognise_is_not_asked(self):
        self.use("on", FakeJev(wall="none"))
        mw, stats = _middleware()
        page = "<html><head><title>Just a moment...</title></head><body>wait</body></html>"
        response = _html(page)
        mw.process_response(response.request, response)
        self.assertEqual(self.jev.calls, [])
        self.assertEqual(stats.get_value(stat_key(BlockKind.CHALLENGE)), 1)

    def test_only_200_pages_are_asked(self):
        self.use("on", FakeJev(wall="captcha"))
        mw, _ = _middleware()
        response = _html("<html>Nerasta</html>", status=404)
        mw.process_response(response.request, response)
        self.assertEqual(self.jev.calls, [])

    def test_a_crawl_asks_at_most_max_wall_checks_times(self):
        self.use("shadow", FakeJev(wall="none"))
        mw, _ = _middleware()
        for i in range(MAX_WALL_CHECKS + 5):
            response = _html(SHORT_PAGE, url=f"{URL}{i}")
            mw.process_response(response.request, response)
        self.assertEqual(len(self.jev.calls), MAX_WALL_CHECKS)


if __name__ == "__main__":
    unittest.main()
