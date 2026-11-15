"""The unchanged-item counter reaches the ScrapeRun row (stat nt/items_unchanged)."""

import pytest
from django.utils import timezone

from apps.listings.models import ScrapeRun
from apps.listings.scrape_runs import summarize_stats


def test_summarize_stats_reads_unchanged_counter():
    fields = summarize_stats(
        {
            "finish_reason": "finished",
            "item_scraped_count": 10,
            "nt/items_new": 2,
            "nt/items_updated": 3,
            "nt/items_unchanged": 5,
        }
    )
    assert fields["items_unchanged"] == 5
    assert fields["items_new"] == 2
    assert fields["items_updated"] == 3


def test_summarize_stats_unchanged_defaults_to_zero():
    assert summarize_stats({"finish_reason": "finished"})["items_unchanged"] == 0


@pytest.mark.django_db
def test_unchanged_count_persists_on_scrape_run():
    run = ScrapeRun.objects.create(spider="domoplius", started_at=timezone.now())
    ScrapeRun.objects.filter(pk=run.pk).update(
        **summarize_stats({"finish_reason": "finished", "nt/items_unchanged": 7})
    )
    run.refresh_from_db()
    assert run.items_unchanged == 7
