"""Scrapy extensions for realestate_spiders."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import timedelta

import django_setup

django_setup.setup()

from django.utils import timezone  # noqa: E402
from scrapy import Spider, signals  # noqa: E402
from scrapy.crawler import Crawler  # noqa: E402

logger = logging.getLogger(__name__)

# Push live counts to the /scans page every N items, so a running scan shows progress.
FLUSH_EVERY = 10
STALE_AFTER_H = 6


class ScrapeRunRecorder:
    """Record every spider run as a ScrapeRun row (shown on the frontend /scans page).

    A failure here is logged and never breaks the crawl.
    """

    def __init__(self, crawler: Crawler) -> None:
        self.stats = crawler.stats
        self.run = None
        self.drop_reasons: Counter[str] = Counter()
        self.seen = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> ScrapeRunRecorder:
        ext = cls(crawler)
        crawler.signals.connect(ext.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(ext.spider_closed, signal=signals.spider_closed)
        crawler.signals.connect(ext.item_seen, signal=signals.item_scraped)
        crawler.signals.connect(ext.item_dropped, signal=signals.item_dropped)
        return ext

    def spider_opened(self, spider: Spider) -> None:
        from apps.listings.models import ScrapeRun

        args = {k: str(getattr(spider, k)) for k in ("max_pages",) if hasattr(spider, k)}
        try:
            # A run killed mid-crawl never reaches spider_closed; don't leave it "running".
            ScrapeRun.objects.filter(
                spider=spider.name,
                status=ScrapeRun.Status.RUNNING,
                started_at__lt=timezone.now() - timedelta(hours=STALE_AFTER_H),
            ).update(status=ScrapeRun.Status.FAILED, finish_reason="interrupted")
            self.run = ScrapeRun.objects.create(
                spider=spider.name, started_at=timezone.now(), args=args
            )
        except Exception:
            logger.warning("Could not create ScrapeRun for %s", spider.name, exc_info=True)

    def item_seen(self, item, response, spider: Spider) -> None:
        self._tick()

    def item_dropped(self, item, response, exception, spider: Spider) -> None:
        from apps.listings.scrape_runs import normalize_drop_reason

        self.drop_reasons[normalize_drop_reason(str(exception))] += 1
        self._tick()

    def _tick(self) -> None:
        self.seen += 1
        if self.seen % FLUSH_EVERY == 0:
            self._save({**self.stats.get_stats(), "finish_reason": "finished"}, running=True)

    def spider_closed(self, spider: Spider, reason: str) -> None:
        self._save({**self.stats.get_stats(), "finish_reason": reason}, running=False)

    def _save(self, stats: dict, running: bool) -> None:
        if self.run is None:
            return
        from apps.listings.models import ScrapeRun
        from apps.listings.scrape_runs import summarize_stats

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
