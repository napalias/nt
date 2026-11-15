"""The beat schedule: scan entries come from the source registry, everything else stays."""

import json

import pytest
from celery.schedules import crontab
from celery.signals import beat_init
from django_celery_beat.models import CrontabSchedule, PeriodicTask, PeriodicTasks

from apps.listings.sources import RUN_SPIDER_TASK, SOURCES, beat_entry_name, scheduled_sources
from config.celery import app as celery_app
from config.celery import prune_stale_scan_tasks

NON_SCAN_ENTRIES = {
    "nightly-db-backup": "apps.listings.tasks.nightly_db_backup",
    "classify-new-listings": "apps.classifier.tasks.classify_new_listings",
    "cleanup-descriptions": "apps.classifier.tasks.cleanup_descriptions",
    "dedup-nightly": "apps.classifier.tasks.cluster_listings",
    "check-listing-validity": "apps.listings.tasks.check_listing_validity",
    "notify-saved-searches": "apps.search.tasks.notify_saved_searches",
}

SCHEDULE = celery_app.conf.beat_schedule


def _scan_entries() -> dict[str, dict]:
    return {name: e for name, e in SCHEDULE.items() if e["task"] == RUN_SPIDER_TASK}


def test_every_enabled_source_has_a_beat_entry():
    expected = {beat_entry_name(s.spider) for s in scheduled_sources()}
    assert set(_scan_entries()) == expected
    for source in scheduled_sources():
        entry = SCHEDULE[beat_entry_name(source.spider)]
        assert entry["args"] == [source.spider]
        assert entry["schedule"] == crontab(**source.schedule.crontab_kwargs())


def test_disabled_sources_are_not_scheduled():
    for source in SOURCES:
        if not source.enabled:
            assert beat_entry_name(source.spider) not in SCHEDULE


def test_scan_entries_pass_no_hardcoded_args():
    # Args live in the registry; run_spider reads them, so the beat entry carries none.
    for entry in _scan_entries().values():
        assert not entry.get("kwargs")


@pytest.mark.parametrize(("name", "task"), NON_SCAN_ENTRIES.items())
def test_non_scan_entries_are_kept(name: str, task: str):
    assert SCHEDULE[name]["task"] == task


def test_no_entry_uses_the_folded_gov_tasks():
    tasks = {e["task"] for e in SCHEDULE.values()}
    assert "apps.permits.tasks.run_infostatyba_spider" not in tasks
    assert "apps.planning.tasks.scrape_tpdris" not in tasks


def _periodic(name: str, task: str, args: list | None = None) -> PeriodicTask:
    cron, _ = CrontabSchedule.objects.get_or_create(minute="0", hour="6")
    return PeriodicTask.objects.create(
        name=name, task=task, crontab=cron, args=json.dumps(args or [])
    )


@pytest.mark.django_db
class TestPruneStaleScanTasks:
    """DatabaseScheduler never deletes rows dropped from beat_schedule, so old national
    scans would keep firing. Beat start prunes scan rows the registry no longer owns."""

    def _seed(self) -> None:
        _periodic("scrape-domoplius", RUN_SPIDER_TASK, ["domoplius"])
        _periodic("scrape-skelbiu-daily", RUN_SPIDER_TASK, ["skelbiu"])
        _periodic("scrape-tpdris-weekly", "apps.planning.tasks.scrape_tpdris")
        _periodic("scrape-infostatyba-daily", "apps.permits.tasks.run_infostatyba_spider")
        _periodic("nightly-db-backup", "apps.listings.tasks.nightly_db_backup")
        _periodic("hand-made", "apps.classifier.tasks.cluster_listings")

    def test_deletes_only_stale_scan_rows(self):
        self._seed()
        removed = prune_stale_scan_tasks({"scrape-domoplius", "nightly-db-backup"})
        assert removed == [
            "scrape-infostatyba-daily",
            "scrape-skelbiu-daily",
            "scrape-tpdris-weekly",
        ]
        assert set(PeriodicTask.objects.values_list("name", flat=True)) == {
            "scrape-domoplius",
            "nightly-db-backup",
            "hand-made",
        }

    def test_tells_the_running_scheduler_to_reload(self):
        self._seed()
        PeriodicTasks.objects.all().delete()
        prune_stale_scan_tasks(set())
        assert PeriodicTasks.last_change() is not None

    def test_nothing_stale_changes_nothing(self):
        _periodic("scrape-domoplius", RUN_SPIDER_TASK, ["domoplius"])
        PeriodicTasks.objects.all().delete()
        assert prune_stale_scan_tasks({"scrape-domoplius"}) == []
        assert PeriodicTasks.last_change() is None

    def test_runs_when_beat_starts(self):
        self._seed()
        beat_init.send(sender=None)
        names = set(PeriodicTask.objects.values_list("name", flat=True))
        assert "scrape-skelbiu-daily" not in names
        assert "scrape-tpdris-weekly" not in names
        assert "nightly-db-backup" in names
