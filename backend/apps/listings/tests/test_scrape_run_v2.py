"""ScrapeRun v2 (#8): partial/blocked statuses from crawl stats, block kinds, needs-human,
links seen, and the spider args read from the `scrapy crawl` command line."""

import pytest
from django.utils import timezone

from apps.listings.blocks import NEEDS_HUMAN, PRIORITY, BlockKind, stat_key
from apps.listings.models import ScrapeRun
from apps.listings.scrape_runs import clip_start_url, spider_args_from_argv, summarize_stats


def _stats(**kw) -> dict:
    base = {"finish_reason": "finished", "response_received_count": 3}
    for key, value in kw.items():
        base[key.replace("__", "/")] = value
    return base


class TestBlockKinds:
    def test_model_choices_cover_every_kind(self):
        assert {value for value, _ in ScrapeRun.BLOCK_KIND_CHOICES} == {k.value for k in BlockKind}

    def test_priority_lists_every_kind_once(self):
        assert sorted(PRIORITY) == sorted(BlockKind)

    def test_needs_human_kinds(self):
        assert NEEDS_HUMAN == {
            BlockKind.CAPTCHA,
            BlockKind.CHALLENGE,
            BlockKind.LOGIN,
            BlockKind.ROBOTS,
        }

    def test_stat_key(self):
        assert stat_key(BlockKind.CAPTCHA) == "nt/block/captcha"


class TestStatuses:
    def test_clean_run_is_ok(self):
        out = summarize_stats({**_stats(), "item_scraped_count": 10})
        assert out["status"] == ScrapeRun.Status.OK
        assert out["block_kind"] == ""
        assert out["needs_human"] is False

    def test_errors_with_items_saved_is_partial(self):
        out = summarize_stats({**_stats(), "item_scraped_count": 10, "log_count/ERROR": 2})
        assert out["status"] == ScrapeRun.Status.PARTIAL

    def test_403_with_items_saved_is_partial(self):
        stats = {
            **_stats(),
            "item_scraped_count": 10,
            "downloader/response_status_count/403": 1,
        }
        out = summarize_stats(stats)
        assert out["status"] == ScrapeRun.Status.PARTIAL
        assert out["block_kind"] == BlockKind.FORBIDDEN

    def test_more_than_30_percent_dropped_is_partial(self):
        stats = {**_stats(), "item_scraped_count": 6, "item_dropped_count": 4}
        assert summarize_stats(stats)["status"] == ScrapeRun.Status.PARTIAL

    def test_exactly_30_percent_dropped_is_ok(self):
        stats = {**_stats(), "item_scraped_count": 7, "item_dropped_count": 3}
        assert summarize_stats(stats)["status"] == ScrapeRun.Status.OK

    def test_challenge_page_served_with_200_is_blocked_not_empty(self):
        # The bug this issue fixes: a PerimeterX page answered 200 and counted as "empty".
        stats = {**_stats(), stat_key(BlockKind.CAPTCHA): 1}
        out = summarize_stats(stats)
        assert out["status"] == ScrapeRun.Status.BLOCKED
        assert out["block_kind"] == BlockKind.CAPTCHA
        assert out["needs_human"] is True

    def test_robots_forbidden_start_url_is_blocked(self):
        stats = {
            "finish_reason": "finished",
            "robotstxt/forbidden": 1,
            stat_key(BlockKind.ROBOTS): 1,
        }
        out = summarize_stats(stats)
        assert out["status"] == ScrapeRun.Status.BLOCKED
        assert out["block_kind"] == BlockKind.ROBOTS
        assert out["needs_human"] is True

    def test_429_with_nothing_saved_is_blocked_rate_limited(self):
        out = summarize_stats({**_stats(), "downloader/response_status_count/429": 4})
        assert out["status"] == ScrapeRun.Status.BLOCKED
        assert out["block_kind"] == BlockKind.RATE_LIMITED
        assert out["needs_human"] is False

    def test_401_means_login(self):
        out = summarize_stats({**_stats(), "downloader/response_status_count/401": 1})
        assert out["block_kind"] == BlockKind.LOGIN
        assert out["needs_human"] is True

    def test_most_actionable_kind_wins(self):
        stats = {
            **_stats(),
            stat_key(BlockKind.FORBIDDEN): 5,
            stat_key(BlockKind.CAPTCHA): 1,
            stat_key(BlockKind.RATE_LIMITED): 2,
        }
        assert summarize_stats(stats)["block_kind"] == BlockKind.CAPTCHA

    def test_block_mid_crawl_with_items_saved_is_partial_and_keeps_kind(self):
        stats = {**_stats(), "item_scraped_count": 10, stat_key(BlockKind.CAPTCHA): 1}
        out = summarize_stats(stats)
        assert out["status"] == ScrapeRun.Status.PARTIAL
        assert out["block_kind"] == BlockKind.CAPTCHA
        assert out["needs_human"] is True

    def test_spider_can_ask_for_a_human(self):
        out = summarize_stats({**_stats(), "nt/needs_human": 1})
        assert out["needs_human"] is True
        assert out["status"] == ScrapeRun.Status.EMPTY

    def test_failed_finish_wins_over_block(self):
        stats = {"finish_reason": "shutdown", stat_key(BlockKind.CAPTCHA): 1}
        out = summarize_stats(stats)
        assert out["status"] == ScrapeRun.Status.FAILED
        assert out["block_kind"] == BlockKind.CAPTCHA

    def test_nothing_at_all_is_empty(self):
        assert summarize_stats({"finish_reason": "finished"})["status"] == ScrapeRun.Status.EMPTY

    def test_scrapy_robots_close_is_blocked_not_failed(self):
        # Scrapy closes a crawl with "robotstxt_denied" when robots.txt forbade every start
        # URL. That alone names the block, even without the middleware's stat.
        stats = {"finish_reason": "robotstxt_denied", "robotstxt/forbidden": 1}
        out = summarize_stats(stats)
        assert out["status"] == ScrapeRun.Status.BLOCKED
        assert out["block_kind"] == BlockKind.ROBOTS
        assert out["needs_human"] is True
        assert out["finish_reason"] == "robotstxt_denied"

    def test_other_abnormal_close_is_still_failed(self):
        out = summarize_stats({"finish_reason": "closespider_timeout"})
        assert out["status"] == ScrapeRun.Status.FAILED
        assert out["block_kind"] == ""


class TestStartUrl:
    def test_short_url_is_kept(self):
        assert clip_start_url("https://x.lt/a") == "https://x.lt/a"

    def test_long_url_is_cut_to_the_column(self):
        url = "https://x.lt/?q=" + "a" * 1000
        clipped = clip_start_url(url)
        assert clipped == url[: ScrapeRun.START_URL_MAX_LENGTH]
        field = ScrapeRun._meta.get_field("start_url")
        assert len(clipped) == field.max_length == 500

    @pytest.mark.django_db
    def test_clipped_url_fits_the_database_column(self):
        url = clip_start_url("https://x.lt/?q=" + "a" * 1000)
        run = ScrapeRun.objects.create(spider="domoplius", started_at=timezone.now(), start_url=url)
        run.refresh_from_db()
        assert run.start_url == url


class TestLinksSeen:
    def test_spider_reported_count_wins(self):
        stats = {**_stats(), "nt/links_seen": 42, "scheduler/enqueued": 5}
        assert summarize_stats(stats)["links_seen"] == 42

    def test_falls_back_to_requests_enqueued(self):
        assert summarize_stats({**_stats(), "scheduler/enqueued": 5})["links_seen"] == 5

    def test_defaults_to_zero(self):
        assert summarize_stats({"finish_reason": "finished"})["links_seen"] == 0


class TestSpiderArgsFromArgv:
    def test_reads_every_a_pair(self):
        argv = [
            "/usr/local/bin/scrapy",
            "crawl",
            "domoplius",
            "-s",
            "DOWNLOAD_DELAY=3",
            "-a",
            "region=kretinga",
            "-amax_pages=2",
            "-a",
            "q=a=b",
        ]
        assert spider_args_from_argv(argv) == {
            "region": "kretinga",
            "max_pages": "2",
            "q": "a=b",
        }

    def test_not_a_crawl_command_is_none(self):
        assert spider_args_from_argv(["python", "-m", "unittest"]) is None

    def test_crawl_without_args_is_empty(self):
        assert spider_args_from_argv(["scrapy", "crawl", "domoplius"]) == {}

    def test_malformed_pair_is_skipped(self):
        assert spider_args_from_argv(["scrapy", "crawl", "x", "-a", "novalue", "-a"]) == {}


@pytest.mark.django_db
def test_new_fields_persist():
    run = ScrapeRun.objects.create(
        spider="domoplius",
        started_at="2026-11-15T06:00:00Z",
        trigger=ScrapeRun.Trigger.BEAT,
        start_url="https://domoplius.lt/skelbimai/namai-kotedzai-sodai/kretinga?page=1",
        links_seen=12,
        block_kind=BlockKind.CAPTCHA,
        needs_human=True,
        log_tail="INFO: Spider closed (finished)",
        status=ScrapeRun.Status.PARTIAL,
    )
    run.refresh_from_db()
    assert run.trigger == "beat"
    assert run.block_kind == "captcha"
    assert run.needs_human is True
    assert run.notified_at is None
    assert run.get_status_display()
