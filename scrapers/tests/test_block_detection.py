"""Block detection (#8): challenge, captcha and login pages, including HTTP 200 ones, plus
403/429 answers and robots-forbidden start URLs, end up as Scrapy stats the ScrapeRun reads.

Run inside the scraper container (stdlib only):
    docker compose exec scraper_worker python -m unittest discover -s tests -v
"""

import unittest

from apps.listings.blocks import BlockKind, stat_key
from scrapy import Request
from scrapy.exceptions import IgnoreRequest
from scrapy.http import HtmlResponse, Response
from scrapy.utils.test import get_crawler

from realestate_spiders.middlewares import BlockDetectionMiddleware, classify_response

URL = "https://www.aruodas.lt/namai/kretingoje/"

PERIMETERX_PAGE = """<html><head><title>Access to this page has been denied</title>
<script>window._pxAppId = 'PXabc';</script></head>
<body><div id="px-captcha"></div><p>Press &amp; Hold to confirm you are a human</p></body></html>"""

CLOUDFLARE_PAGE = """<!DOCTYPE html><html><head><title>Just a moment...</title></head>
<body><script>window._cf_chl_opt={cvId:'3'};</script>Checking your browser</body></html>"""

DATADOME_PAGE = """<html><head><title>aruodas.lt</title></head><body>
<iframe src="https://geo.captcha-delivery.com/captcha/?initialCid=x"></iframe></body></html>"""

LOGIN_PAGE = """<html><head><title>Prisijungimas | RaskNT</title></head><body>
<form method="post"><input name="user"><input type="password" name="pass"></form>
</body></html>"""

# A real listing page: a recaptcha on the contact form, Cloudflare's bot-detection script
# and a header login modal must not count as blocks.
LISTING_PAGE = """<html><head><title>Namas Kretingoje, 120 m² | Domoplius</title>
<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>
<script src="https://www.google.com/recaptcha/api.js"></script></head>
<body><div class="login-modal"><input type="password" name="pw"></div>
<h1>Parduodamas namas</h1><div class="g-recaptcha" data-sitekey="x"></div></body></html>"""


class ClassifyResponseTest(unittest.TestCase):
    def test_perimeterx_page_served_with_200_is_captcha(self) -> None:
        self.assertEqual(classify_response(200, URL, PERIMETERX_PAGE), BlockKind.CAPTCHA)

    def test_perimeterx_page_served_with_403_is_captcha_not_forbidden(self) -> None:
        self.assertEqual(classify_response(403, URL, PERIMETERX_PAGE), BlockKind.CAPTCHA)

    def test_datadome_is_captcha(self) -> None:
        self.assertEqual(classify_response(200, URL, DATADOME_PAGE), BlockKind.CAPTCHA)

    def test_cloudflare_just_a_moment_is_challenge(self) -> None:
        for status in (200, 403, 503):
            with self.subTest(status=status):
                self.assertEqual(
                    classify_response(status, URL, CLOUDFLARE_PAGE), BlockKind.CHALLENGE
                )

    def test_login_page_is_login(self) -> None:
        self.assertEqual(classify_response(200, URL, LOGIN_PAGE), BlockKind.LOGIN)

    def test_redirect_to_a_login_url_is_login(self) -> None:
        final = "https://rasknt.lt/prisijungti?next=/namai/kretinga/"
        self.assertEqual(
            classify_response(200, final, "<html></html>", redirected=True), BlockKind.LOGIN
        )

    def test_login_url_without_redirect_is_not_a_block(self) -> None:
        # The spider asked for that URL itself; nothing pushed it there.
        self.assertIsNone(classify_response(200, "https://x.lt/login", "<html></html>"))

    def test_author_path_is_not_a_login_url(self) -> None:
        self.assertIsNone(
            classify_response(200, "https://x.lt/author/jonas", "<html></html>", redirected=True)
        )

    def test_401_is_login(self) -> None:
        self.assertEqual(classify_response(401, URL, ""), BlockKind.LOGIN)

    def test_429_is_rate_limited(self) -> None:
        self.assertEqual(classify_response(429, URL, "Too Many Requests"), BlockKind.RATE_LIMITED)

    def test_plain_403_is_forbidden(self) -> None:
        self.assertEqual(classify_response(403, URL, "<h1>Forbidden</h1>"), BlockKind.FORBIDDEN)

    def test_listing_page_is_not_a_block(self) -> None:
        self.assertIsNone(classify_response(200, URL, LISTING_PAGE))

    def test_binary_or_empty_body_uses_the_status_only(self) -> None:
        self.assertIsNone(classify_response(200, URL, None))
        self.assertEqual(classify_response(403, URL, None), BlockKind.FORBIDDEN)

    def test_404_is_not_a_block(self) -> None:
        self.assertIsNone(classify_response(404, URL, "<h1>Not found</h1>"))


def _middleware() -> tuple[BlockDetectionMiddleware, object]:
    crawler = get_crawler()
    crawler.stats.open_spider()
    return BlockDetectionMiddleware.from_crawler(crawler), crawler.stats


def _html(url: str, body: str, status: int = 200, meta: dict | None = None) -> HtmlResponse:
    request = Request(url, meta=meta or {})
    return HtmlResponse(url, status=status, body=body, encoding="utf-8", request=request)


class BlockDetectionMiddlewareTest(unittest.TestCase):
    def test_challenge_page_is_counted_and_passed_on_unchanged(self) -> None:
        mw, stats = _middleware()
        response = _html(URL, PERIMETERX_PAGE)
        self.assertIs(mw.process_response(response.request, response), response)
        self.assertEqual(stats.get_value(stat_key(BlockKind.CAPTCHA)), 1)

    def test_normal_page_records_nothing(self) -> None:
        mw, stats = _middleware()
        response = _html(URL, LISTING_PAGE)
        mw.process_response(response.request, response)
        self.assertFalse(
            [k for k in stats.get_stats() if k.startswith("nt/block/")], stats.get_stats()
        )

    def test_redirected_login_page(self) -> None:
        mw, stats = _middleware()
        response = _html(
            "https://rasknt.lt/login",
            "<html></html>",
            meta={"redirect_urls": ["https://rasknt.lt/namai/kretinga/"]},
        )
        mw.process_response(response.request, response)
        self.assertEqual(stats.get_value(stat_key(BlockKind.LOGIN)), 1)

    def test_non_text_403_is_forbidden(self) -> None:
        mw, stats = _middleware()
        request = Request(URL)
        response = Response(URL, status=403, body=b"\x00\x01", request=request)
        mw.process_response(request, response)
        self.assertEqual(stats.get_value(stat_key(BlockKind.FORBIDDEN)), 1)

    def test_robots_forbidden_start_url(self) -> None:
        mw, stats = _middleware()
        request = Request(URL, meta={"is_start_request": True})
        self.assertIsNone(mw.process_exception(request, IgnoreRequest("Forbidden by robots.txt")))
        self.assertEqual(stats.get_value(stat_key(BlockKind.ROBOTS)), 1)

    def test_robots_forbidden_detail_page_is_not_a_start_url_block(self) -> None:
        mw, stats = _middleware()
        request = Request(URL + "1234/")
        mw.process_exception(request, IgnoreRequest("Forbidden by robots.txt"))
        self.assertIsNone(stats.get_value(stat_key(BlockKind.ROBOTS)))

    def test_other_errors_are_ignored(self) -> None:
        mw, stats = _middleware()
        request = Request(URL, meta={"is_start_request": True})
        mw.process_exception(request, IgnoreRequest("Filtered offsite request"))
        mw.process_exception(request, TimeoutError("slow"))
        self.assertIsNone(stats.get_value(stat_key(BlockKind.ROBOTS)))

    def test_is_enabled_after_decompression(self) -> None:
        # process_response runs from high to low order: it must sit below
        # HttpCompressionMiddleware (590) to see decoded HTML, above MetaRefreshMiddleware
        # (580) to see a meta-refresh page before it is followed, and above
        # RetryMiddleware (550) to see every 429 before it is retried.
        from scrapy.utils.project import get_project_settings

        order = get_project_settings().getdict("DOWNLOADER_MIDDLEWARES")
        position = order["realestate_spiders.middlewares.BlockDetectionMiddleware"]
        self.assertTrue(580 < position < 590, position)


if __name__ == "__main__":
    unittest.main()
