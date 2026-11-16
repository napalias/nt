"""Open scan problems (/api/scrapes/attention) and the e-mail that tells the principal.

Mail goes to Django's locmem backend (pytest-django swaps it in), never to Mailpit or SMTP.
conftest sets NOTIFY_EMAIL to a placeholder address.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import Client
from django.utils import timezone
from model_bakery import baker

from apps.listings.blocks import BlockKind
from apps.listings.models import ScrapeRun
from apps.listings.notify import ProblemKind, open_problems, send_problem_notifications
from apps.listings.tasks import notify_scrape_problems

pytestmark = pytest.mark.django_db

S = ScrapeRun.Status


def _run(spider: str, hours_ago: float, status: str, **kw) -> ScrapeRun:
    started = timezone.now() - timedelta(hours=hours_ago)
    return baker.make(
        ScrapeRun,
        spider=spider,
        status=status,
        started_at=started,
        finished_at=None if status == S.RUNNING else started + timedelta(minutes=5),
        **kw,
    )


def _by_spider() -> dict:
    return {p.source.spider: p for p in open_problems()}


class TestOpenProblems:
    def test_no_runs_no_problems(self):
        assert open_problems() == []

    def test_ok_latest_run_closes_an_older_problem(self):
        _run("domoplius", 30, S.FAILED)
        _run("domoplius", 2, S.OK)
        assert open_problems() == []

    def test_blocked_captcha_needs_a_human(self):
        _run("aruodas", 1, S.BLOCKED, block_kind=BlockKind.CAPTCHA, needs_human=True)
        problem = _by_spider()["aruodas"]
        assert problem.kind == ProblemKind.NEEDS_HUMAN
        assert problem.label == "Aruodas.lt"  # registry label
        assert "solve the captcha in a browser" in problem.what_to_do.lower()
        assert problem.notifies is True

    def test_blocked_without_a_human_step(self):
        _run("domoplius", 1, S.BLOCKED, block_kind=BlockKind.RATE_LIMITED)
        problem = _by_spider()["domoplius"]
        assert problem.kind == ProblemKind.BLOCKED
        assert "delay" in problem.what_to_do.lower()

    def test_failed(self):
        _run("domoplius", 1, S.FAILED, finish_reason="timeout")
        problem = _by_spider()["domoplius"]
        assert problem.kind == ProblemKind.FAILED
        assert "timeout" in problem.what_to_do

    def test_one_empty_run_is_not_a_problem(self):
        _run("domoplius", 30, S.OK)
        _run("domoplius", 1, S.EMPTY)
        assert open_problems() == []

    def test_two_empty_runs_in_a_row(self):
        _run("domoplius", 30, S.EMPTY)
        _run("domoplius", 1, S.EMPTY)
        problem = _by_spider()["domoplius"]
        assert problem.kind == ProblemKind.EMPTY_TWICE
        assert problem.notifies is True

    def test_partial_is_listed_but_does_not_mail(self):
        _run("domoplius", 1, S.PARTIAL, errors=3)
        problem = _by_spider()["domoplius"]
        assert problem.kind == ProblemKind.PARTIAL
        assert problem.notifies is False

    def test_running_run_is_skipped_for_the_last_finished_one(self):
        _run("domoplius", 2, S.FAILED)
        _run("domoplius", 0, S.RUNNING)
        assert _by_spider()["domoplius"].kind == ProblemKind.FAILED

    def test_running_run_does_not_break_an_empty_streak(self):
        _run("domoplius", 30, S.EMPTY)
        _run("domoplius", 2, S.EMPTY)
        _run("domoplius", 0, S.RUNNING)
        assert _by_spider()["domoplius"].kind == ProblemKind.EMPTY_TWICE

    def test_unregistered_spider_is_ignored(self):
        _run("test", 1, S.FAILED)
        assert open_problems() == []

    def test_problems_follow_registry_order(self):
        _run("tpdris", 1, S.FAILED)
        _run("aruodas", 1, S.BLOCKED, block_kind=BlockKind.CAPTCHA, needs_human=True)
        assert [p.source.spider for p in open_problems()] == ["aruodas", "tpdris"]

    @pytest.mark.parametrize(
        ("kind", "phrase"),
        [
            (BlockKind.CAPTCHA, "captcha"),
            (BlockKind.CHALLENGE, "bot check"),
            (BlockKind.LOGIN, "log"),
            (BlockKind.ROBOTS, "robots.txt"),
            (BlockKind.FORBIDDEN, "403"),
            (BlockKind.RATE_LIMITED, "429"),
        ],
    )
    def test_every_block_kind_has_a_what_to_do_line(self, kind, phrase):
        _run("domoplius", 1, S.BLOCKED, block_kind=kind)
        assert phrase in _by_spider()["domoplius"].what_to_do.lower()


class TestNotifications:
    def test_mails_the_principal_once(self, mailoutbox, settings):
        _run("aruodas", 1, S.BLOCKED, block_kind=BlockKind.CAPTCHA, needs_human=True)
        _run("domoplius", 1, S.FAILED, finish_reason="shutdown")
        result = send_problem_notifications()
        assert sorted(result["notified"]) == ["aruodas", "domoplius"]
        assert len(mailoutbox) == 1
        mail = mailoutbox[0]
        assert mail.to == [settings.NOTIFY_EMAIL]
        assert "Aruodas.lt" in mail.subject
        assert "Domoplius.lt" in mail.body
        assert "solve the captcha in a browser" in mail.body.lower()
        assert ScrapeRun.objects.filter(notified_at__isnull=False).count() == 2

    def test_same_problem_is_not_mailed_twice(self, mailoutbox):
        _run("domoplius", 1, S.FAILED)
        send_problem_notifications()
        assert send_problem_notifications()["notified"] == []
        assert len(mailoutbox) == 1

    def test_at_most_once_per_source_per_24h(self, mailoutbox):
        _run("domoplius", 5, S.FAILED, notified_at=timezone.now() - timedelta(hours=4))
        _run("domoplius", 1, S.FAILED)  # a new problem run, but within the 24 h window
        assert send_problem_notifications()["notified"] == []
        assert mailoutbox == []

    def test_mails_again_after_24h(self, mailoutbox):
        _run("domoplius", 30, S.FAILED, notified_at=timezone.now() - timedelta(hours=25))
        _run("domoplius", 1, S.FAILED)
        assert send_problem_notifications()["notified"] == ["domoplius"]
        assert len(mailoutbox) == 1

    def test_throttle_is_per_source(self, mailoutbox):
        _run("domoplius", 5, S.FAILED, notified_at=timezone.now() - timedelta(hours=4))
        _run("domoplius", 1, S.FAILED)
        _run("tpdris", 1, S.FAILED)
        assert send_problem_notifications()["notified"] == ["tpdris"]

    def test_two_empty_runs_mail(self, mailoutbox):
        _run("domoplius", 30, S.EMPTY)
        _run("domoplius", 1, S.EMPTY)
        assert send_problem_notifications()["notified"] == ["domoplius"]

    def test_partial_does_not_mail(self, mailoutbox):
        _run("domoplius", 1, S.PARTIAL)
        assert send_problem_notifications()["notified"] == []
        assert mailoutbox == []

    def test_no_recipient_skips_with_a_log_line_and_keeps_the_problem_pending(
        self, mailoutbox, settings, caplog
    ):
        settings.NOTIFY_EMAIL = ""
        run = _run("domoplius", 1, S.FAILED)
        with caplog.at_level("INFO", logger="apps.listings.notify"):
            result = send_problem_notifications()
        assert result["notified"] == []
        assert result["skipped"] == "NOTIFY_EMAIL is not set"
        assert "NOTIFY_EMAIL" in caplog.text
        assert mailoutbox == []
        run.refresh_from_db()
        assert run.notified_at is None

    def test_send_failure_leaves_the_problem_pending(self, mailoutbox):
        run = _run("domoplius", 1, S.FAILED)
        with (
            patch("apps.listings.notify.send_mail", side_effect=OSError("smtp down")),
            pytest.raises(OSError),
        ):
            send_problem_notifications()
        run.refresh_from_db()
        assert run.notified_at is None

    def test_celery_task_runs_the_check(self, mailoutbox):
        _run("domoplius", 1, S.FAILED)
        assert notify_scrape_problems()["notified"] == ["domoplius"]
        assert len(mailoutbox) == 1


class TestAttentionApi:
    def test_lists_open_problems_with_what_to_do(self):
        _run("aruodas", 1, S.BLOCKED, block_kind=BlockKind.CAPTCHA, needs_human=True)
        _run("domoplius", 1, S.OK)
        data = Client().get("/api/scrapes/attention").json()
        assert [row["spider"] for row in data] == ["aruodas"]
        row = data[0]
        assert row["label"] == "Aruodas.lt"
        assert row["problem"] == "needs_human"
        assert row["severity"] == "error"
        assert "solve the captcha in a browser" in row["what_to_do"].lower()
        assert row["run"]["block_kind"] == "captcha"
        assert row["run"]["needs_human"] is True
        assert row["notified_at"] is None

    def test_partial_is_a_warning(self):
        _run("domoplius", 1, S.PARTIAL)
        row = Client().get("/api/scrapes/attention").json()[0]
        assert row["severity"] == "warning"

    def test_nothing_open(self):
        assert Client().get("/api/scrapes/attention").json() == []


class TestRunsApi:
    def test_runs_carry_the_new_fields(self):
        _run(
            "domoplius",
            1,
            S.PARTIAL,
            trigger=ScrapeRun.Trigger.BEAT,
            args={"region": "kretinga", "max_pages": "10"},
            start_url="https://domoplius.lt/x",
            links_seen=30,
            items_unchanged=4,
            log_tail="secret-free log",
        )
        row = Client().get("/api/scrapes/runs").json()[0]
        assert row["trigger"] == "beat"
        assert row["args"] == {"region": "kretinga", "max_pages": "10"}
        assert row["start_url"] == "https://domoplius.lt/x"
        assert row["links_seen"] == 30
        assert row["items_unchanged"] == 4
        assert row["block_kind"] == ""
        assert row["needs_human"] is False
        assert "log_tail" not in row  # the list stays light; the detail has it

    def test_run_detail_has_the_log_tail(self):
        run = _run("domoplius", 1, S.OK, log_tail="INFO: Spider closed (finished)")
        data = Client().get(f"/api/scrapes/runs/{run.id}").json()
        assert data["log_tail"] == "INFO: Spider closed (finished)"
        assert Client().get("/api/scrapes/runs/999999").status_code == 404
