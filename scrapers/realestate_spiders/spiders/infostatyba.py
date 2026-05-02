"""Infostatyba spider — scrapes building permits from planuojustatyti.lt.

Usage:
    scrapy crawl infostatyba
    scrapy crawl infostatyba -a max_pages=2          # debug: limit pages
    scrapy crawl infostatyba -a days_back=30          # only last 30 days

The site is JS-rendered, so this spider uses scrapy-playwright.
It searches by date range and paginates through results.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import scrapy
from scrapy.http import HtmlResponse

from realestate_spiders.items import PermitItem


class InfostatybaSpider(scrapy.Spider):
    name = "infostatyba"
    allowed_domains = ["planuojustatyti.lt"]

    custom_settings = {
        "DOWNLOAD_DELAY": 5,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "ROBOTSTXT_OBEY": False,  # site blocks robots.txt but data is public
    }

    def __init__(
        self,
        max_pages: int = 0,
        days_back: int = 365,
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.max_pages = int(max_pages)
        self.days_back = int(days_back)
        self.current_page = 1

    def start_requests(self):
        date_to = datetime.now(UTC).strftime("%Y-%m-%d")
        date_from = (datetime.now(UTC) - timedelta(days=self.days_back)).strftime("%Y-%m-%d")

        search_url = (
            "https://www.planuojustatyti.lt/statybos-leidimu-paieska"
            f"?dateFrom={date_from}&dateTo={date_to}&page=1"
        )

        self.logger.info("Starting Infostatyba search: %s to %s", date_from, date_to)

        yield scrapy.Request(
            search_url,
            callback=self.parse_search_results,
            meta={
                "playwright": True,
                "playwright_include_page": False,
                "playwright_page_goto_kwargs": {"wait_until": "networkidle", "timeout": 30000},
            },
        )

    def parse_search_results(self, response: HtmlResponse):
        """Parse the search results page and follow detail links."""
        # Look for permit rows/links in the search results table
        detail_links = response.css(
            "table a[href*='/statybos-leidimas/']::attr(href), "
            ".search-results a[href*='/statybos-leidimas/']::attr(href), "
            "a[href*='/leidimas/']::attr(href), "
            "a[href*='/permit/']::attr(href)"
        ).getall()

        # Deduplicate while preserving order
        detail_links = list(dict.fromkeys(detail_links))

        self.logger.info(
            "Page %d: found %d permit links",
            self.current_page,
            len(detail_links),
        )

        if not detail_links:
            self.logger.info("No permits found on page %d, stopping", self.current_page)
            return

        for link in detail_links:
            url = response.urljoin(link)
            yield scrapy.Request(
                url,
                callback=self.parse_permit_detail,
                meta={
                    "playwright": True,
                    "playwright_include_page": False,
                    "playwright_page_goto_kwargs": {
                        "wait_until": "networkidle",
                        "timeout": 30000,
                    },
                },
            )

        # Paginate
        self.current_page += 1
        if self.max_pages and self.current_page > self.max_pages:
            self.logger.info("Reached max_pages=%d, stopping", self.max_pages)
            return

        next_link = response.css(
            "a[rel='next']::attr(href), "
            ".pagination a.next::attr(href), "
            "a[aria-label='Next']::attr(href)"
        ).get()

        if next_link:
            yield scrapy.Request(
                response.urljoin(next_link),
                callback=self.parse_search_results,
                meta={
                    "playwright": True,
                    "playwright_include_page": False,
                    "playwright_page_goto_kwargs": {
                        "wait_until": "networkidle",
                        "timeout": 30000,
                    },
                },
            )

    def parse_permit_detail(self, response: HtmlResponse):
        """Parse a single permit detail page."""
        info = {}
        # Try to extract key-value pairs from table rows or definition lists
        for row in response.css("table tr, .detail-row, .info-row, dl"):
            key = (
                row.css("th::text, td:first-child::text, dt::text, .label::text")
                .get("")
                .strip()
                .lower()
            )
            value = (
                row.css("td:last-child::text, td:nth-child(2)::text, dd::text, .value::text")
                .get("")
                .strip()
            )
            if key and value:
                info[key] = value

        permit_number = self._get_field(
            info, ["leidimo nr", "leidimo numeris", "numeris", "nr."]
        ) or self._extract_permit_number_from_url(response.url)

        if not permit_number:
            self.logger.warning("No permit number found at %s", response.url)
            return

        permit_type = self._get_field(info, ["leidimo tipas", "dokumento tipas", "tipas"])
        status = self._get_field(info, ["statusas", "būsena", "status"])
        issued_at = self._get_field(
            info, ["išdavimo data", "data", "leidimo data", "patvirtinimo data"]
        )

        applicant = self._get_field(info, ["statytojas", "užsakovas", "pareiškėjas", "applicant"])

        cadastral = self._get_field(
            info, ["kadastro nr", "kadastro numeris", "sklypo nr", "žemės sklypo"]
        )
        address = self._get_field(info, ["adresas", "vieta", "address"])

        description = self._get_field(
            info,
            [
                "statinio pavadinimas",
                "projekto pavadinimas",
                "aprašymas",
                "pavadinimas",
            ],
        )
        project_type = self._get_field(info, ["statybos rūšis", "darbų rūšis", "statybos tipas"])
        building_purpose = self._get_field(
            info, ["paskirtis", "pastato paskirtis", "statinio paskirtis"]
        )

        yield PermitItem(
            permit_number=permit_number.strip(),
            permit_type=permit_type or "",
            status=status or "",
            issued_at=issued_at or "",
            applicant_name=applicant or "",
            cadastral_number=cadastral or "",
            address_raw=address or "",
            project_description=description or "",
            project_type=project_type or "",
            building_purpose=building_purpose or "",
            source_url=response.url,
            raw_data=info,
            scraped_at=datetime.now(UTC).isoformat(),
        )

    def _get_field(self, info: dict, keys: list[str]) -> str:
        """Look up a value by trying multiple key prefixes."""
        for key in keys:
            for k, v in info.items():
                if key in k:
                    return v
        return ""

    def _extract_permit_number_from_url(self, url: str) -> str:
        """Try to extract a permit number from the URL path."""
        match = re.search(r"/(?:leidimas|permit)[/-]([A-Za-z0-9\-]+)", url)
        return match.group(1) if match else ""
