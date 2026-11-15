import logging
import os
from collections.abc import Iterable

from celery import Celery
from celery.schedules import crontab
from celery.signals import beat_init

from apps.listings.sources import RUN_SPIDER_TASK, beat_entry_name, scheduled_sources

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

logger = logging.getLogger(__name__)

app = Celery("realestate")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Scrape schedules are owned by the source registry (apps/listings/sources.py): edit it,
# not the admin. Spider args, delays and property types are read by run_spider at run time.
SCAN_ENTRIES = {
    beat_entry_name(source.spider): {
        "task": RUN_SPIDER_TASK,
        "schedule": crontab(**source.schedule.crontab_kwargs()),
        "args": [source.spider],
    }
    for source in scheduled_sources()
}

OTHER_ENTRIES = {
    "nightly-db-backup": {
        "task": "apps.listings.tasks.nightly_db_backup",
        "schedule": crontab(hour=3, minute=0),
    },
    "classify-new-listings": {
        "task": "apps.classifier.tasks.classify_new_listings",
        "schedule": crontab(hour=9, minute=0),
        "kwargs": {"limit": 50},
    },
    "cleanup-descriptions": {
        "task": "apps.classifier.tasks.cleanup_descriptions",
        "schedule": crontab(hour=9, minute=30),
        "kwargs": {"limit": 50},
    },
    "dedup-nightly": {
        "task": "apps.classifier.tasks.cluster_listings",
        "schedule": crontab(hour=10, minute=0),
    },
    "check-listing-validity": {
        "task": "apps.listings.tasks.check_listing_validity",
        "schedule": crontab(hour=11, minute=0),
        "kwargs": {"batch_size": 100},
    },
    "notify-saved-searches": {
        "task": "apps.search.tasks.notify_saved_searches",
        "schedule": crontab(minute="*/30"),
    },
}

app.conf.beat_schedule = {**SCAN_ENTRIES, **OTHER_ENTRIES}

# Tasks whose beat rows the registry owns. The two gov tasks were folded into run_spider;
# their names stay here only so their old rows get pruned.
MANAGED_SCAN_TASKS = (
    RUN_SPIDER_TASK,
    "apps.permits.tasks.run_infostatyba_spider",
    "apps.planning.tasks.scrape_tpdris",
)


def prune_stale_scan_tasks(keep: Iterable[str]) -> list[str]:
    """Delete scan rows from the DatabaseScheduler table that are not in `keep`.

    django-celery-beat copies beat_schedule into the DB but never removes entries dropped
    from it, so a disabled or renamed source would keep firing. Returns the deleted names.
    """
    from django_celery_beat.models import PeriodicTask, PeriodicTasks

    stale = PeriodicTask.objects.filter(task__in=MANAGED_SCAN_TASKS).exclude(name__in=set(keep))
    names = sorted(stale.values_list("name", flat=True))
    if names:
        stale.delete()
        # The running scheduler reloads from the DB when this timestamp moves.
        PeriodicTasks.update_changed()
        logger.info("Pruned stale scan schedules: %s", ", ".join(names))
    return names


@beat_init.connect
def _prune_on_beat_start(sender: object = None, **kwargs: object) -> None:
    try:
        prune_stale_scan_tasks(app.conf.beat_schedule)
    except Exception:
        # Beat must keep running the backups and other jobs; say loudly what is left.
        logger.exception("Could not prune stale scan schedules; removed sources may still run")
