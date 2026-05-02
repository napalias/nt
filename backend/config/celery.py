import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("realestate")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "nightly-db-backup": {
        "task": "apps.listings.tasks.nightly_db_backup",
        "schedule": crontab(hour=3, minute=0),
    },
    "scrape-domoplius-daily": {
        "task": "apps.listings.tasks.run_spider",
        "schedule": crontab(hour=6, minute=0),
        "args": ["domoplius"],
        "kwargs": {"max_pages": 10},
    },
    "scrape-skelbiu-daily": {
        "task": "apps.listings.tasks.run_spider",
        "schedule": crontab(hour=7, minute=0),
        "args": ["skelbiu"],
        "kwargs": {"max_pages": 10},
    },
    "scrape-aruodas-daily": {
        "task": "apps.listings.tasks.run_spider",
        "schedule": crontab(hour=8, minute=0),
        "args": ["aruodas"],
        "kwargs": {"max_pages": 5},
    },
    "classify-new-listings": {
        "task": "apps.classifier.tasks.classify_new_listings",
        "schedule": crontab(hour=9, minute=0),
        "kwargs": {"limit": 50},
    },
    "dedup-nightly": {
        "task": "apps.classifier.tasks.cluster_listings",
        "schedule": crontab(hour=10, minute=0),
    },
    "notify-saved-searches": {
        "task": "apps.search.tasks.notify_saved_searches",
        "schedule": crontab(minute="*/30"),
    },
    "scrape-tpdris-weekly": {
        "task": "apps.planning.tasks.scrape_tpdris",
        "schedule": crontab(hour=4, minute=0, day_of_week="sunday"),
        "kwargs": {"max_pages": 50},
    },
    "scrape-infostatyba-daily": {
        "task": "apps.permits.tasks.run_infostatyba_spider",
        "schedule": crontab(hour=5, minute=0),
        "kwargs": {"max_pages": 20},
    },
}
