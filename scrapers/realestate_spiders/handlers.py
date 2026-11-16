"""Download handler that fetches with curl_cffi's browser TLS fingerprint instead of a browser.

Some portals answer plain Scrapy requests with a 403 wall but serve the same page to a client
that looks like Chrome at the TLS level (skelbiu listing pages are one). `inspect_site`
calls this fetch mode `curl_cffi`: a real fingerprint, no JavaScript, no browser process.

A spider opts in through its own settings, so every other spider keeps its handler:

    custom_settings = {
        "DOWNLOAD_HANDLERS": {
            "http": "realestate_spiders.handlers.CurlCffiDownloadHandler",
            "https": "realestate_spiders.handlers.CurlCffiDownloadHandler",
        },
    }

It is only the transport. Delays, retries, cookies, robots.txt and redirects stay Scrapy's
(the handler never follows a redirect itself), so DOWNLOAD_DELAY and the politeness rules still
apply. It does not solve challenges: a captcha page comes back as the 403/200 body it is.

The `User-Agent` request header is not forwarded: curl_cffi sends the one that matches the
fingerprint it impersonates, and a mismatched pair is itself a bot signal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from curl_cffi import requests as curl_requests
from curl_cffi.requests import exceptions as curl_exceptions
from scrapy.core.downloader.handlers.base import BaseDownloadHandler
from scrapy.http import Headers, Response
from scrapy.responsetypes import responsetypes
from twisted.internet.error import ConnectError, TimeoutError

if TYPE_CHECKING:
    from scrapy import Request
    from scrapy.crawler import Crawler

IMPERSONATE = "chrome"
# The body is already decoded and its length is no longer the wire length.
DROPPED_RESPONSE_HEADERS = {"content-encoding", "content-length", "transfer-encoding"}
DROPPED_REQUEST_HEADERS = {"user-agent"}


class CurlCffiDownloadHandler(BaseDownloadHandler):
    def __init__(self, crawler: Crawler) -> None:
        super().__init__(crawler)
        self._session: curl_requests.AsyncSession | None = None

    def _get_session(self) -> curl_requests.AsyncSession:
        if self._session is None:
            self._session = curl_requests.AsyncSession(impersonate=IMPERSONATE)
        return self._session

    async def download_request(self, request: Request) -> Response:
        timeout = request.meta.get("download_timeout") or self.crawler.settings.getfloat(
            "DOWNLOAD_TIMEOUT", 180
        )
        headers = {
            k.decode(): b", ".join(v).decode()
            for k, v in request.headers.items()
            if k.decode().lower() not in DROPPED_REQUEST_HEADERS
        }
        try:
            reply = await self._get_session().request(
                request.method,
                request.url,
                headers=headers or None,
                data=request.body or None,
                timeout=timeout,
                allow_redirects=False,
            )
        except curl_exceptions.Timeout as exc:
            raise TimeoutError(f"curl_cffi timed out after {timeout}s: {request.url}") from exc
        except (curl_exceptions.ConnectionError, curl_exceptions.SSLError) as exc:
            raise ConnectError(string=f"curl_cffi could not connect: {exc}") from exc

        response_headers: dict[str, list[str]] = {}
        for key, value in reply.headers.multi_items():
            if key.lower() not in DROPPED_RESPONSE_HEADERS:
                response_headers.setdefault(key, []).append(value)
        scrapy_headers = Headers(response_headers)
        response_class = responsetypes.from_args(
            headers=scrapy_headers, url=reply.url, body=reply.content
        )
        return response_class(
            url=request.url,
            status=reply.status_code,
            headers=scrapy_headers,
            body=reply.content,
            request=request,
        )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
