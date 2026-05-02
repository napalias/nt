"""Bootstrap Django ORM for use in Scrapy pipelines.

Call setup() before importing any Django models. This is intended to run
inside the scrapers Docker container where the backend code is mounted
at /backend.
"""

import os
import sys

import django


def setup() -> None:
    sys.path.insert(0, "/backend")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
    django.setup()
