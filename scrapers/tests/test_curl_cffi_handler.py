"""CurlCffiDownloadHandler against a throwaway local HTTP server (no internet).

Run inside the scraper container (stdlib unittest, no pytest in that image):
    docker compose exec scraper_worker python -m unittest discover -s tests
"""

import asyncio
import gzip
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from scrapy import Request
from scrapy.http import HtmlResponse
from scrapy.utils.test import get_crawler
from twisted.internet.error import ConnectError, TimeoutError

from realestate_spiders.handlers import CurlCffiDownloadHandler

PAGE = "<html><head><title>Namas</title></head><body><h1>Šiltas namas</h1></body></html>"


class Handler(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def log_message(self, *args) -> None:  # keep test output quiet
        pass

    def do_GET(self) -> None:
        self.seen.append({"path": self.path, "headers": dict(self.headers)})
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.end_headers()
        elif self.path == "/blocked":
            body = b"<html>blocked</html>"
            self.send_response(403)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/slow":
            threading.Event().wait(2)
            self.send_response(200)
            self.end_headers()
        else:
            body = gzip.compress(PAGE.encode("utf-8"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


class HandlerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        Handler.seen.clear()
        self.handler = CurlCffiDownloadHandler(get_crawler())

    def fetch(self, path: str, **kwargs):
        async def run():
            try:
                return await self.handler.download_request(Request(self.base + path, **kwargs))
            finally:
                await self.handler.close()

        return asyncio.run(run())

    def test_returns_a_decoded_html_response(self) -> None:
        response = self.fetch("/page")
        self.assertIsInstance(response, HtmlResponse)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.css("h1::text").get(), "Šiltas namas")
        # the body is already decoded, so a second decompression must not be attempted
        self.assertNotIn(b"content-encoding", response.headers)
        self.assertEqual(response.url, self.base + "/page")

    def test_a_redirect_is_returned_not_followed(self) -> None:
        response = self.fetch("/redirect")
        self.assertEqual(response.status, 302)
        self.assertEqual(response.headers["Location"], b"/page")
        self.assertEqual([r["path"] for r in Handler.seen], ["/redirect"])

    def test_an_error_status_comes_back_as_a_response(self) -> None:
        self.assertEqual(self.fetch("/blocked").status, 403)

    def test_the_scrapy_user_agent_is_not_forwarded_but_other_headers_are(self) -> None:
        self.fetch(
            "/page", headers={"User-Agent": "Scrapy-test-agent", "Referer": "https://x.test/"}
        )
        sent = {k.lower(): v for k, v in Handler.seen[0]["headers"].items()}
        self.assertNotEqual(sent.get("user-agent"), "Scrapy-test-agent")
        self.assertIn("chrome", sent["user-agent"].lower())
        self.assertEqual(sent["referer"], "https://x.test/")

    def test_a_timeout_is_a_retryable_scrapy_error(self) -> None:
        with self.assertRaises(TimeoutError):
            self.fetch("/slow", meta={"download_timeout": 0.5})

    def test_a_refused_connection_is_a_connect_error(self) -> None:
        async def run():
            try:
                request = Request("http://127.0.0.1:9/")
                return await self.handler.download_request(request)
            finally:
                await self.handler.close()

        with self.assertRaises(ConnectError):
            asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
