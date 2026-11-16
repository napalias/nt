"""The data migration that scrubs contact data from existing listings.

Runs the migration function against the pytest test DB with baker-made rows holding fake
+370 numbers. It is never applied to the real database from here.
"""

import importlib
import json

import pytest
from django.apps import apps as django_apps
from django.db import connection
from model_bakery import baker

from apps.listings.models import Listing
from apps.listings.scrub import MAX_RAW_DATA_BYTES

migration = importlib.import_module("apps.listings.migrations.0005_scrub_contact_data")

FAKE_PHONE = "+370 600 00001"


def _dirty_raw_data():
    return {
        "namo tipas": "Mūrinis",
        "šildymas": "Dujinis",
        "page_json": json.dumps(
            {"contact": {"phone": FAKE_PHONE, "name": "Jonas"}, "mobile": "+37060000002"}
        ),
        "contact": {"phone": FAKE_PHONE},
        "seller": {"email": "pardavejas@example.lt", "phone": "+370 600 00003"},
        "blob": "x" * 70_000,
    }


def _dirty_listing(**kw):
    return baker.make(
        Listing,
        description=f"Gražus namas. Tel. {FAKE_PHONE}, rašyti a@example.lt. Kaina 120 000 EUR.",
        title=f"Namas {FAKE_PHONE}",
        raw_data=_dirty_raw_data(),
        **kw,
    )


def _count_plus370():
    with connection.cursor() as cur:
        cur.execute(r"SELECT count(*) FROM listings_listing WHERE raw_data::text ~ '\+370'")
        return cur.fetchone()[0]


@pytest.mark.django_db
class TestScrubMigration:
    def test_scrubs_raw_data_description_and_title(self):
        listing = _dirty_listing()
        assert _count_plus370() == 1

        migration.scrub_contact_data(django_apps, None)

        assert _count_plus370() == 0
        listing.refresh_from_db()
        text = json.dumps(listing.raw_data, ensure_ascii=False)
        assert "+370" not in text
        assert "contact" not in listing.raw_data
        assert "seller" not in listing.raw_data
        assert listing.raw_data["namo tipas"] == "Mūrinis"
        assert len(text.encode()) <= MAX_RAW_DATA_BYTES
        assert "370" not in listing.description
        assert "@" not in listing.description
        assert "Kaina 120 000 EUR" in listing.description
        assert "370" not in listing.title

    def test_description_raw_backfilled_from_scrubbed_description(self):
        listing = _dirty_listing()
        migration.scrub_contact_data(django_apps, None)
        listing.refresh_from_db()
        assert listing.description_raw == listing.description
        assert listing.description_clean == ""

    def test_idempotent(self):
        listing = _dirty_listing()
        migration.scrub_contact_data(django_apps, None)
        listing.refresh_from_db()
        first = (listing.raw_data, listing.description, listing.description_raw, listing.title)

        migration.scrub_contact_data(django_apps, None)
        listing.refresh_from_db()
        assert (
            listing.raw_data,
            listing.description,
            listing.description_raw,
            listing.title,
        ) == first

    def test_clean_rows_untouched_including_last_seen(self):
        listing = baker.make(
            Listing, description="Namas be kontaktų", raw_data={"namo tipas": "Mūrinis"}
        )
        before = Listing.objects.get(pk=listing.pk)

        migration.scrub_contact_data(django_apps, None)

        after = Listing.objects.get(pk=listing.pk)
        assert after.raw_data == before.raw_data
        assert after.description == before.description
        assert after.last_seen_at == before.last_seen_at

    def test_existing_description_clean_is_not_overwritten(self):
        listing = baker.make(
            Listing,
            description="Švarus tekstas",
            description_raw="SKUBIAI!!! Švarus tekstas",
            description_clean="Švarus tekstas",
            raw_data={},
        )
        migration.scrub_contact_data(django_apps, None)
        listing.refresh_from_db()
        assert listing.description_raw == "SKUBIAI!!! Švarus tekstas"
        assert listing.description_clean == "Švarus tekstas"

    def test_reverse_is_a_no_op(self):
        listing = _dirty_listing()
        migration.scrub_contact_data(django_apps, None)
        listing.refresh_from_db()
        scrubbed = listing.raw_data

        migration.noop_reverse(django_apps, None)

        listing.refresh_from_db()
        assert listing.raw_data == scrubbed

    def test_migration_is_runpython_with_noop_reverse(self):
        (op,) = migration.Migration.operations
        assert op.code is migration.scrub_contact_data
        assert op.reverse_code is migration.noop_reverse
