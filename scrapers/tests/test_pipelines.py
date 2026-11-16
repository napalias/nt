"""Tests for the listing pipelines: contact scrub, location precision, unchanged detection,
description raw/clean handling, and the permit item fields.

Run inside the scraper container (stdlib only, no pytest in that image):
    docker compose exec scraper_worker python -m unittest discover -s tests -v

The database is never touched: the Listing and permit models are mocked. All phone numbers
are fake.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

import requests
from scrapy.exceptions import DropItem

from realestate_spiders.items import ListingItem, PermitItem
from realestate_spiders.pipelines import (
    DjangoWritePipeline,
    GeocodePipeline,
    NormalizePipeline,
    PermitMatchDeveloperPipeline,
    PermitMatchPlotPipeline,
    ScrubPipeline,
    precision_from_nominatim,
)

PHONE = "+370 600 00001"


class FakeStats:
    def __init__(self):
        self.values = {}

    def inc_value(self, key, count=1, **kw):
        self.values[key] = self.values.get(key, 0) + count

    def get_value(self, key, default=None):
        return self.values.get(key, default)


def make_spider():
    spider = MagicMock()
    spider.name = "domoplius"
    spider.crawler.stats = FakeStats()
    return spider


def make_item(**overrides):
    item = ListingItem(
        url="https://domoplius.lt/skelbimai/namas-123.html",
        source="domoplius",
        title="Namas Kretingoje",
        description="Geras namas. Kaina 120 000 EUR.",
        address="Kretinga",
        city="Kretinga",
        property_type="house",
        listing_type="sale",
        price="120000",
        area_sqm="100",
        latitude=55.88,
        longitude=21.24,
        scraped_at="2026-11-15T10:00:00+00:00",
        raw_data={"namo tipas": "Mūrinis"},
    )
    for key, value in overrides.items():
        item[key] = value
    return item


class ScrubPipelineTest(unittest.TestCase):
    def setUp(self):
        self.pipeline = ScrubPipeline()
        self.spider = make_spider()

    def test_contact_keys_removed_from_raw_data(self):
        item = make_item(
            raw_data={
                "price": "1",
                "contact": {"phone": PHONE},
                "seller": {"email": "a@example.lt"},
                "page": json.dumps({"phone": PHONE}),
            }
        )
        out = self.pipeline.process_item(item, self.spider)
        dumped = json.dumps(out["raw_data"], ensure_ascii=False)
        self.assertNotIn("contact", out["raw_data"])
        self.assertNotIn("seller", out["raw_data"])
        self.assertNotIn("+370", dumped)
        self.assertNotIn("@", dumped)
        self.assertEqual(out["raw_data"]["price"], "1")

    def test_phone_and_email_stripped_from_description_and_title(self):
        item = make_item(
            title=f"Namas {PHONE}",
            description=f"Gražus namas. Tel. {PHONE}, rašyti a@example.lt. Kaina 120 000 EUR.",
        )
        out = self.pipeline.process_item(item, self.spider)
        self.assertNotIn("370", out["title"])
        self.assertNotIn("370", out["description"])
        self.assertNotIn("@", out["description"])
        self.assertIn("Kaina 120 000 EUR", out["description"])

    def test_raw_data_size_capped(self):
        item = make_item(raw_data={"small": "ok", "huge": "x" * 80_000})
        out = self.pipeline.process_item(item, self.spider)
        self.assertLessEqual(len(json.dumps(out["raw_data"]).encode()), 20_000)
        self.assertEqual(out["raw_data"]["small"], "ok")
        self.assertNotIn("huge", out["raw_data"])

    def test_missing_raw_data_becomes_empty_dict(self):
        item = make_item()
        del item["raw_data"]
        out = self.pipeline.process_item(item, self.spider)
        self.assertEqual(out["raw_data"], {})

    def test_permit_items_pass_through(self):
        permit = PermitItem(permit_number="1", raw_data={"phone": PHONE})
        out = self.pipeline.process_item(permit, self.spider)
        self.assertIs(out, permit)
        self.assertEqual(out["raw_data"], {"phone": PHONE})


class ContentHashTest(unittest.TestCase):
    def setUp(self):
        self.pipeline = NormalizePipeline()
        self.spider = make_spider()

    def _hash(self, **overrides):
        return self.pipeline.process_item(make_item(**overrides), self.spider)["_content_hash"]

    def test_same_content_same_hash(self):
        self.assertEqual(self._hash(), self._hash())

    def test_price_change_changes_hash(self):
        self.assertNotEqual(self._hash(), self._hash(price="110000"))

    def test_description_change_changes_hash(self):
        self.assertNotEqual(self._hash(), self._hash(description="Visai kitas tekstas"))

    def test_scraped_at_does_not_change_hash(self):
        self.assertEqual(self._hash(), self._hash(scraped_at="2026-11-16T10:00:00+00:00"))


class PrecisionFromNominatimTest(unittest.TestCase):
    def test_by_place_rank(self):
        self.assertEqual(precision_from_nominatim({"place_rank": 30}), "exact")
        self.assertEqual(precision_from_nominatim({"place_rank": 26}), "street")
        self.assertEqual(precision_from_nominatim({"place_rank": 27}), "street")
        self.assertEqual(precision_from_nominatim({"place_rank": 19}), "city")
        self.assertEqual(precision_from_nominatim({"place_rank": 16}), "city")

    def test_by_class_when_rank_missing(self):
        self.assertEqual(precision_from_nominatim({"class": "building", "type": "house"}), "exact")
        road = {"class": "highway", "type": "residential"}
        self.assertEqual(precision_from_nominatim(road), "street")
        self.assertEqual(precision_from_nominatim({"class": "place", "type": "village"}), "city")

    def test_unknown_is_city(self):
        self.assertEqual(precision_from_nominatim({}), "city")


class GeocodePrecisionTest(unittest.TestCase):
    def setUp(self):
        self.pipeline = GeocodePipeline()
        self.spider = make_spider()

    def test_source_coordinates_are_exact(self):
        out = self.pipeline.process_item(make_item(), self.spider)
        self.assertEqual(out["location_precision"], "exact")

    def _geocode(self, payload=None, error=None):
        item = make_item()
        del item["latitude"]
        del item["longitude"]
        response = MagicMock()
        response.json.return_value = payload if payload is not None else []
        with patch("realestate_spiders.pipelines.requests.get") as get:
            if error:
                get.side_effect = error
            else:
                get.return_value = response
            return self.pipeline.process_item(item, self.spider)

    def test_nominatim_street_hit(self):
        out = self._geocode([{"lat": "55.9", "lon": "21.2", "place_rank": 26}])
        self.assertEqual(out["location_precision"], "street")
        self.assertEqual(out["latitude"], 55.9)

    def test_nominatim_house_hit(self):
        out = self._geocode([{"lat": "55.9", "lon": "21.2", "place_rank": 30}])
        self.assertEqual(out["location_precision"], "exact")

    def test_nominatim_village_hit(self):
        out = self._geocode([{"lat": "55.9", "lon": "21.2", "place_rank": 19}])
        self.assertEqual(out["location_precision"], "city")

    def test_city_centre_fallback_is_city(self):
        out = self._geocode(error=requests.RequestException("down"))
        self.assertEqual(out["location_precision"], "city")
        self.assertEqual((out["latitude"], out["longitude"]), (55.8835, 21.2420))

    def test_no_result_falls_back_to_city(self):
        out = self._geocode([])
        self.assertEqual(out["location_precision"], "city")

    def test_unknown_place_still_dropped(self):
        item = make_item(address="Nežinomas kaimas", city="Nežinomas")
        del item["latitude"]
        del item["longitude"]
        with patch(
            "realestate_spiders.pipelines.requests.get",
            side_effect=requests.RequestException("down"),
        ):
            with self.assertRaises(DropItem):
                self.pipeline.process_item(item, self.spider)


def prepared_item(**overrides):
    """An item as it reaches DjangoWritePipeline."""
    spider = make_spider()
    item = ScrubPipeline().process_item(make_item(**overrides), spider)
    item = NormalizePipeline().process_item(item, spider)
    item["location_precision"] = "exact"
    return item


def existing_row(item, **overrides):
    row = MagicMock()
    row.pk = 7
    row.content_hash = item["_content_hash"]
    row.is_active = True
    row.description_raw = item["description"]
    row.description_clean = ""
    row.description = item["description"]
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


class WritePipelineTest(unittest.TestCase):
    def setUp(self):
        self.pipeline = DjangoWritePipeline()
        self.spider = make_spider()
        patcher = patch("apps.listings.models.Listing")
        self.Listing = patcher.start()
        self.addCleanup(patcher.stop)
        self.created_obj = MagicMock(pk=1, title="Namas")
        self.Listing.objects.update_or_create.return_value = (self.created_obj, True)

    def _existing(self, row):
        self.Listing.objects.filter.return_value.first.return_value = row

    def _defaults(self):
        return self.Listing.objects.update_or_create.call_args.kwargs["defaults"]

    def test_new_item_is_created_with_raw_description_and_precision(self):
        self._existing(None)
        item = prepared_item()
        self.pipeline.process_item(item, self.spider)

        defaults = self._defaults()
        self.assertEqual(defaults["description"], item["description"])
        self.assertEqual(defaults["description_raw"], item["description"])
        self.assertEqual(defaults["description_clean"], "")
        self.assertEqual(defaults["location_precision"], "exact")
        self.assertEqual(self.spider.crawler.stats.get_value("nt/items_new"), 1)
        self.assertIsNone(self.spider.crawler.stats.get_value("nt/items_unchanged"))

    def test_unchanged_item_is_counted_and_not_rewritten(self):
        item = prepared_item()
        self._existing(existing_row(item))

        self.pipeline.process_item(item, self.spider)

        self.Listing.objects.update_or_create.assert_not_called()
        self.assertEqual(self.spider.crawler.stats.get_value("nt/items_unchanged"), 1)
        self.assertIsNone(self.spider.crawler.stats.get_value("nt/items_updated"))
        self.assertIsNone(self.spider.crawler.stats.get_value("nt/items_new"))
        # the row is only touched to record that it was seen again
        update = self.Listing.objects.filter.return_value.update
        update.assert_called_once()
        self.assertEqual(set(update.call_args.kwargs), {"scraped_at", "last_seen_at", "is_active"})

    def test_changed_item_is_updated_not_unchanged(self):
        item = prepared_item()
        self._existing(existing_row(item, content_hash="old-hash"))
        self.Listing.objects.update_or_create.return_value = (self.created_obj, False)

        self.pipeline.process_item(item, self.spider)

        self.Listing.objects.update_or_create.assert_called_once()
        self.assertEqual(self.spider.crawler.stats.get_value("nt/items_updated"), 1)
        self.assertIsNone(self.spider.crawler.stats.get_value("nt/items_unchanged"))

    def test_inactive_row_with_same_hash_is_reactivated_not_unchanged(self):
        item = prepared_item()
        self._existing(existing_row(item, is_active=False))
        self.Listing.objects.update_or_create.return_value = (self.created_obj, False)

        self.pipeline.process_item(item, self.spider)

        self.Listing.objects.update_or_create.assert_called_once()
        self.assertIsNone(self.spider.crawler.stats.get_value("nt/items_unchanged"))

    def test_clean_description_survives_rescrape_when_raw_is_unchanged(self):
        item = prepared_item()
        row = existing_row(
            item,
            content_hash="old-hash",
            description_clean="Švarus tekstas",
            description="Švarus tekstas",
        )
        self._existing(row)
        self.Listing.objects.update_or_create.return_value = (self.created_obj, False)

        self.pipeline.process_item(item, self.spider)

        defaults = self._defaults()
        self.assertNotIn("description", defaults)
        self.assertNotIn("description_clean", defaults)
        self.assertEqual(defaults["description_raw"], item["description"])

    def test_clean_description_reset_when_raw_changes(self):
        item = prepared_item(description="Visiškai naujas aprašymas")
        row = existing_row(
            item,
            content_hash="old-hash",
            description_raw="Senas aprašymas",
            description_clean="Švarus senas",
            description="Švarus senas",
        )
        self._existing(row)
        self.Listing.objects.update_or_create.return_value = (self.created_obj, False)

        self.pipeline.process_item(item, self.spider)

        defaults = self._defaults()
        self.assertEqual(defaults["description"], "Visiškai naujas aprašymas")
        self.assertEqual(defaults["description_raw"], "Visiškai naujas aprašymas")
        self.assertEqual(defaults["description_clean"], "")

    def test_raw_data_never_carries_contact_data_into_the_row(self):
        self._existing(None)
        item = prepared_item(raw_data={"contact": {"phone": PHONE}, "k": "v"})
        self.pipeline.process_item(item, self.spider)
        self.assertEqual(self._defaults()["raw_data"], {"k": "v"})


class PermitItemTest(unittest.TestCase):
    def test_pipeline_internal_fields_are_declared(self):
        item = PermitItem(permit_number="1", source_url="https://example.lt/1")
        item["_plot_id"] = 5
        item["_plot_centroid"] = "POINT (21 55)"
        item["_applicant_id"] = 9
        self.assertEqual(item["_plot_id"], 5)
        self.assertEqual(item["_applicant_id"], 9)

    def test_match_plot_found_sets_fields(self):
        plot = MagicMock(id=11)
        plot.geometry.centroid = "CENTROID"
        with patch("apps.cadastre.models.CadastralPlot") as Plot:
            Plot.DoesNotExist = type("DoesNotExist", (Exception,), {})
            Plot.objects.get.return_value = plot
            item = PermitItem(permit_number="1", cadastral_number="4400-1")
            out = PermitMatchPlotPipeline().process_item(item, make_spider())
        self.assertEqual(out["_plot_id"], 11)
        self.assertEqual(out["_plot_centroid"], "CENTROID")

    def test_match_plot_missing_sets_none_without_keyerror(self):
        with patch("apps.cadastre.models.CadastralPlot") as Plot:
            Plot.DoesNotExist = type("DoesNotExist", (Exception,), {})
            Plot.objects.get.side_effect = Plot.DoesNotExist
            item = PermitItem(permit_number="1", cadastral_number="4400-1")
            out = PermitMatchPlotPipeline().process_item(item, make_spider())
        self.assertIsNone(out["_plot_id"])

    def test_match_developer_by_company_code(self):
        dev = MagicMock(id=3)
        with patch("apps.developers.models.Developer") as Dev:
            Dev.DoesNotExist = type("DoesNotExist", (Exception,), {})
            Dev.objects.get.return_value = dev
            item = PermitItem(permit_number="1", applicant_name="UAB Statyba 123456789")
            out = PermitMatchDeveloperPipeline().process_item(item, make_spider())
        self.assertEqual(out["_applicant_id"], 3)

    def test_match_developer_without_match_sets_none(self):
        with patch("apps.developers.models.Developer") as Dev:
            Dev.objects.filter.return_value.__getitem__.return_value = []
            item = PermitItem(permit_number="1", applicant_name="UAB Niekas")
            out = PermitMatchDeveloperPipeline().process_item(item, make_spider())
        self.assertIsNone(out["_applicant_id"])


if __name__ == "__main__":
    unittest.main()
