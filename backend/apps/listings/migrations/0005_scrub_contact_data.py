"""Scrub contact data (phones, emails, seller/contact keys) from existing listings.

IRREVERSIBLE for the affected rows: the removed text is gone (the listings are re-scrapable).
Do not apply to the real database without the principal's go-ahead.

Idempotent: a second run finds nothing to change and writes nothing. Rows that need no change
are not touched, and the writes use queryset.update() so last_seen_at (auto_now) is left alone.
Also backfills description_raw from the scrubbed description where it is still empty.
"""

from django.db import migrations

from apps.listings.scrub import scrub_raw_data, scrub_text

CHUNK = 200


def scrub_contact_data(apps, schema_editor):
    Listing = apps.get_model("listings", "Listing")
    fields = (
        "pk",
        "title",
        "description",
        "description_raw",
        "description_clean",
        "raw_data",
    )
    for row in Listing.objects.only(*fields).iterator(chunk_size=CHUNK):
        description = scrub_text(row.description)
        changes = {
            "title": scrub_text(row.title),
            "description": description,
            "description_raw": scrub_text(row.description_raw)
            if row.description_raw
            else description,
            "description_clean": scrub_text(row.description_clean),
            "raw_data": scrub_raw_data(row.raw_data),
        }
        changes = {k: v for k, v in changes.items() if v != getattr(row, k)}
        if changes:
            Listing.objects.filter(pk=row.pk).update(**changes)


def noop_reverse(apps, schema_editor):
    """The removed contact data cannot be restored."""


class Migration(migrations.Migration):
    dependencies = [
        ("listings", "0004_listing_precision_and_descriptions"),
    ]

    operations = [
        migrations.RunPython(scrub_contact_data, noop_reverse),
    ]
