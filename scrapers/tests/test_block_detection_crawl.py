"""End to end (#8): a real `scrapy runspider` with the project settings, against a local
HTTP server, records a PerimeterX page served with HTTP 200 and a robots-forbidden start URL
in the crawl stats, and those stats make a blocked, needs-human ScrapeRun.

No network beyond 127.0.0.1, no Playwright (plain HTTP handler), no database (the
ScrapeRunRecorder extension is switched off for this crawl).

    docker compose exec scraper_worker python -m unittest discover -s tests -v
"""

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import django_setup

django_setup.setup()

from apps.listings.blocks import BlockKind, stat_key  # noqa: E402
from apps.listings.models import ScrapeRun  # noqa: E402
from apps.listings.scrape_runs import summarize_stats  # noqa: E402

SCRAPERS_DIR = Path(__file__).resolve().parent.parent

PX_PAGE = (
    b"<html><head><title>Access to this page has been denied</title></head>"
    b'<body><div id="px-captcha"></div></body></html>'
)

SPIDER = """
import json
import scrapy


class ProbeSpider(scrapy.Spider):
    name = "probe"

    async def start(self):
        for path in self.paths.split(","):
            yield scrapy.Request(self.base + path, callback=self.parse)

    def parse(self, response):
        return None

    def closed(self, reason):
        with open(self.out, "w") as f:
            # The real close reason, as the recorder gets it on spider_closed.
            stats = {**self.crawler.stats.get_stats(), "finish_reason": reason}
            json.dump(stats, f, default=str)
"""


class _Site(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path == "/robots.txt":
            body, kind = b"User-agent: *\nDisallow: /private\n", "text/plain"
        else:
            body, kind = PX_PAGE, "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return None


def _crawl(base: str, paths: str, workdir: Path) -> dict:
    spider_file = workdir / "probe_spider.py"
    spider_file.write_text(SPIDER)
    out = workdir / "stats.json"
    plain_http = "scrapy.core.downloader.handlers.http11.HTTP11DownloadHandler"
    cmd = [
        sys.executable,
        "-m",
        "scrapy",
        "runspider",
        str(spider_file),
        "-a",
        f"base={base}",
        "-a",
        f"paths={paths}",
        "-a",
        f"out={out}",
        "-s",
        "DOWNLOAD_DELAY=0",
        "-s",
        "LOG_LEVEL=WARNING",
        "-s",
        json_setting("EXTENSIONS", {"realestate_spiders.extensions.ScrapeRunRecorder": None}),
        "-s",
        json_setting("DOWNLOAD_HANDLERS", {"http": plain_http, "https": plain_http}),
        "-s",
        json_setting("ITEM_PIPELINES", {}),
    ]
    result = subprocess.run(cmd, cwd=SCRAPERS_DIR, capture_output=True, text=True, timeout=120)
    if result.returncode != 0 or not out.exists():
        raise AssertionError(f"crawl failed (exit {result.returncode}):\n{result.stderr[-3000:]}")
    return json.loads(out.read_text())


def json_setting(name: str, value: dict) -> str:
    return f"{name}={json.dumps(value)}"


class BlockDetectionCrawlTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_challenge_page_with_http_200_is_a_blocked_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stats = _crawl(self.base, "/namai/kretinga/", Path(tmp))
        self.assertEqual(stats.get(stat_key(BlockKind.CAPTCHA)), 1, stats)
        self.assertEqual(stats["finish_reason"], "finished")
        fields = summarize_stats(stats)
        self.assertEqual(fields["status"], ScrapeRun.Status.BLOCKED)
        self.assertEqual(fields["block_kind"], "captcha")
        self.assertIs(fields["needs_human"], True)

    def test_robots_forbidden_start_url_is_a_blocked_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stats = _crawl(self.base, "/private/namai/", Path(tmp))
        self.assertEqual(stats.get("robotstxt/forbidden"), 1, stats)
        self.assertEqual(stats.get(stat_key(BlockKind.ROBOTS)), 1, stats)
        # Scrapy stops a crawl whose every start URL robots.txt forbids; that is a block,
        # not a crash, so the run must not come out "failed".
        self.assertEqual(stats["finish_reason"], "robotstxt_denied")
        fields = summarize_stats(stats)
        self.assertEqual(fields["status"], ScrapeRun.Status.BLOCKED)
        self.assertEqual(fields["block_kind"], "robots")
        self.assertIs(fields["needs_human"], True)


if __name__ == "__main__":
    unittest.main()
