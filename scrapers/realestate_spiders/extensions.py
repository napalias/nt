"""Scrapy extensions for realestate_spiders."""

from __future__ import annotations

import logging
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any

import django_setup

django_setup.setup()

from apps.listings.models import ScrapeRun  # noqa: E402
from apps.listings.scrape_runs import (  # noqa: E402
    RUN_ID_SETTING,
    clip_start_url,
    normalize_drop_reason,
    spider_args_from_argv,
    summarize_stats,
)
from django.utils import timezone  # noqa: E402
from scrapy import Request, Spider, signals  # noqa: E402
from scrapy.crawler import Crawler  # noqa: E402

logger = logging.getLogger(__name__)

# Push live counts to the /scans page every N items, so a running scan shows progress.
FLUSH_EVERY = 10
STALE_AFTER_H = 6
# Spider args recorded when a crawl was not started from a `scrapy crawl` command line.
FALLBACK_ARG_NAMES = ("max_pages", "region", "target", "property_type")


class ScrapeRunRecorder:
    """Record every spider run as a ScrapeRun row (shown on the frontend /scans page).

    A crawl started by run_spider fills in the row that task made (setting
    NT_SCRAPE_RUN_ID); a hand-started `scrapy crawl` gets a new row with trigger "manual"
    and the `-a` args of its command line. A failure here is logged and never breaks the
    crawl.

    Each handler is named after its signal and declares only the signal arguments it
    uses: Scrapy passes signal arguments by keyword and hands a handler just the ones it
    names (and warns at connect time about a name the signal never sends).
    """

    def __init__(self, crawler: Crawler) -> None:
        self.stats = crawler.stats
        self.run_id = crawler.settings.getint(RUN_ID_SETTING, 0)
        self.run: ScrapeRun | None = None
        self.start_url_recorded = False
        self.drop_reasons: Counter[str] = Counter()
        self.seen = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> ScrapeRunRecorder:
        ext = cls(crawler)
        crawler.signals.connect(ext.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(ext.request_scheduled, signal=signals.request_scheduled)
        crawler.signals.connect(ext.item_scraped, signal=signals.item_scraped)
        crawler.signals.connect(ext.item_dropped, signal=signals.item_dropped)
        crawler.signals.connect(ext.spider_closed, signal=signals.spider_closed)
        return ext

    def spider_opened(self, spider: Spider) -> None:
        try:
            # A run killed mid-crawl never reaches spider_closed; don't leave it "running".
            ScrapeRun.objects.filter(
                spider=spider.name,
                status=ScrapeRun.Status.RUNNING,
                started_at__lt=timezone.now() - timedelta(hours=STALE_AFTER_H),
            ).update(status=ScrapeRun.Status.FAILED, finish_reason="interrupted")
            if self.run_id:
                self.run = ScrapeRun.objects.filter(pk=self.run_id, spider=spider.name).first()
                if self.run is None:
                    logger.warning(
                        "%s=%d is not a %s run; recording a new row",
                        RUN_ID_SETTING,
                        self.run_id,
                        spider.name,
                    )
            if self.run is None:
                self.run = ScrapeRun.objects.create(
                    spider=spider.name,
                    started_at=timezone.now(),
                    trigger=ScrapeRun.Trigger.MANUAL,
                    args=crawl_args(spider, sys.argv),
                )
        except Exception:
            logger.warning("Could not create ScrapeRun for %s", spider.name, exc_info=True)

    def request_scheduled(self, request: Request) -> None:
        """The first start request is the run's start URL (what was actually asked for).

        Scrapy's StartSpiderMiddleware marks start requests with meta `is_start_request`.
        Runs for every request, so the common case returns at once. It never raises: an
        IgnoreRequest here would drop the request, and any other error would be logged as
        an ERROR and turn an ok run partial."""
        if self.start_url_recorded or self.run is None:
            return
        if not request.meta.get("is_start_request"):
            return
        self.start_url_recorded = True
        try:
            ScrapeRun.objects.filter(pk=self.run.pk).update(start_url=clip_start_url(request.url))
        except Exception:
            logger.warning("Could not record start URL of run %s", self.run.pk, exc_info=True)

    def item_scraped(self) -> None:
        self._tick()

    def item_dropped(self, exception: BaseException) -> None:
        self.drop_reasons[normalize_drop_reason(str(exception))] += 1
        self._tick()

    def spider_closed(self, reason: str) -> None:
        self._save({**self.stats.get_stats(), "finish_reason": reason}, running=False)

    def _tick(self) -> None:
        self.seen += 1
        if self.seen % FLUSH_EVERY == 0:
            self._save({**self.stats.get_stats(), "finish_reason": "finished"}, running=True)

    def _save(self, stats: Mapping[str, Any], running: bool) -> None:
        if self.run is None:
            return
        try:
            fields = summarize_stats(stats)
            if running:
                fields["status"] = ScrapeRun.Status.RUNNING
                fields["finish_reason"] = ""
            else:
                fields["finished_at"] = timezone.now()
            fields["drop_reasons"] = dict(self.drop_reasons.most_common(20))
            ScrapeRun.objects.filter(pk=self.run.pk).update(**fields)
        except Exception:
            logger.warning("Could not update ScrapeRun %s", self.run.pk, exc_info=True)


def crawl_args(spider: Spider, argv: Sequence[str]) -> dict[str, str]:
    """Every `-a` arg of the `scrapy crawl` command line; without one (a crawl started from
    Python), the spider's well-known arg attributes."""
    from_argv = spider_args_from_argv(argv)  # the -a args, or None outside `scrapy crawl`
    if from_argv is not None:
        return from_argv
    return {k: str(getattr(spider, k)) for k in FALLBACK_ARG_NAMES if hasattr(spider, k)}
