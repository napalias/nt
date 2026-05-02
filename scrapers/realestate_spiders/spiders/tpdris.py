"""TPDRIS spider — scrapes territorial planning documents from tpdris.lt.

Usage:
    scrapy crawl tpdris
    scrapy crawl tpdris -a max_pages=2          # debug: limit pages
    scrapy crawl tpdris -a status=approved       # only approved documents

Scrapes the TPDRIS public search for approved territorial planning documents.
Uses playwright because the search interface is JS-rendered.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import scrapy
from scrapy.http import HtmlResponse

from realestate_spiders.items import PlanningItem

DOC_TYPE_MAP = {
    "bendrasis planas": "master",
    "detalusis planas": "detailed",
    "specialusis planas": "special",
    "specialusis": "special",
    "bendrasis": "master",
    "detalusis": "detailed",
}

STATUS_MAP = {
    "rengiamas": "preparation",
    "viešas svarstymas": "public_review",
    "patvirtintas": "approved",
    "atmestas": "rejected",
    "nebegalioja": "expired",
    "galiojantis": "approved",
}


class TpdrisSpider(scrapy.Spider):
    name = "tpdris"
    allowed_domains = ["tpdris.lt"]

    custom_settings = {
        "DOWNLOAD_DELAY": 5,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "ITEM_PIPELINES": {
            "realestate_spiders.pipelines.PlanningWritePipeline": 400,
        },
    }

    def __init__(
        self,
        max_pages: int = 0,
        status: str = "approved",
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.max_pages = int(max_pages)
        self.status = status
        self.current_page = 1

    def start_requests(self):
        url = f"https://www.tpdris.lt/lt/paieska?status={self.status}&page=1"
        yield scrapy.Request(
            url,
            callback=self.parse_search,
            meta={
                "playwright": True,
                "playwright_include_page": False,
                "playwright_page_goto_kwargs": {"wait_until": "networkidle"},
            },
        )

    def parse_search(self, response: HtmlResponse):
        rows = response.css(
            "table.search-results tbody tr, "
            ".search-results .result-item, "
            ".document-list .document-item, "
            "table tbody tr"
        )

        self.logger.info("Page %d: found %d result rows", self.current_page, len(rows))

        if not rows:
            self.logger.info("No results on page %d, stopping", self.current_page)
            return

        for row in rows:
            detail_link = row.css("a[href*='/lt/']::attr(href)").get()
            if not detail_link:
                detail_link = row.css("a::attr(href)").get()
            if not detail_link:
                continue

            detail_url = response.urljoin(detail_link)
            yield scrapy.Request(
                detail_url,
                callback=self.parse_detail,
                meta={
                    "playwright": True,
                    "playwright_include_page": False,
                    "playwright_page_goto_kwargs": {"wait_until": "networkidle"},
                },
            )

        self.current_page += 1
        if self.max_pages and self.current_page > self.max_pages:
            self.logger.info("Reached max_pages=%d, stopping", self.max_pages)
            return

        next_url = re.sub(
            r"page=\d+",
            f"page={self.current_page}",
            response.url,
        )
        yield scrapy.Request(
            next_url,
            callback=self.parse_search,
            meta={
                "playwright": True,
                "playwright_include_page": False,
                "playwright_page_goto_kwargs": {"wait_until": "networkidle"},
            },
        )

    def parse_detail(self, response: HtmlResponse):
        title = response.css("h1::text, h2::text, .document-title::text").get("").strip()
        if not title:
            title = response.css("title::text").get("").strip()

        info_table = {}
        for row in response.css(
            "table tr, .info-block .info-row, dl dt, .detail-info tr, .document-details tr"
        ):
            key = (
                row.css(
                    "th::text, td:first-child::text, dt::text, .label::text, span:first-child::text"
                )
                .get("")
                .strip()
                .lower()
            )
            value = (
                row.css(
                    "td:last-child::text, td:nth-child(2)::text, "
                    "dd::text, .value::text, span:last-child::text"
                )
                .get("")
                .strip()
            )
            if not value:
                sibling = row.xpath("following-sibling::dd[1]/text()").get("")
                value = sibling.strip() if sibling else ""
            if key and value:
                info_table[key] = value

        tpdris_id = self._extract_tpdris_id(response.url, info_table)
        doc_type = self._classify_doc_type(info_table, title)
        status_raw = self._get_field(info_table, ["būsena", "statusas", "status"])
        status = STATUS_MAP.get(status_raw.lower(), self.status) if status_raw else self.status

        municipality = self._get_field(info_table, ["savivaldybė", "municipality", "teritorija"])
        organizer = self._get_field(
            info_table, ["organizatorius", "planavimo organizatorius", "užsakovas"]
        )
        approved_at = self._get_field(info_table, ["patvirtinimo data", "patvirtinta", "data"])
        expires_at = self._get_field(info_table, ["galiojimo pabaiga", "galioja iki"])

        pdf_links = []
        for link in response.css("a[href$='.pdf'], a[href*='.pdf']"):
            href = link.attrib.get("href", "")
            link_title = link.css("::text").get("").strip() or "Document"
            if href:
                pdf_links.append(
                    {
                        "url": response.urljoin(href),
                        "title": link_title,
                    }
                )

        yield PlanningItem(
            tpdris_id=tpdris_id,
            source_url=response.url,
            title=title,
            doc_type=doc_type,
            status=status,
            municipality=municipality,
            organizer=organizer,
            approved_at=self._parse_date(approved_at),
            expires_at=self._parse_date(expires_at),
            pdf_links=pdf_links,
            raw_data=info_table,
            scraped_at=datetime.now(UTC).isoformat(),
        )

    def _extract_tpdris_id(self, url: str, info: dict) -> str:
        """Extract a unique ID from the URL or page metadata."""
        for key in ["numeris", "reg. nr.", "registracijos nr.", "dokumento nr."]:
            for k, v in info.items():
                if key in k and v.strip():
                    return v.strip()

        match = re.search(r"/(\d+)(?:\?|$|/)", url)
        if match:
            return f"TPDRIS-{match.group(1)}"

        import hashlib

        return f"TPDRIS-{hashlib.md5(url.encode()).hexdigest()[:12]}"

    def _classify_doc_type(self, info: dict, title: str) -> str:
        """Determine document type from metadata or title."""
        type_raw = self._get_field(info, ["dokumento rūšis", "rūšis", "tipas", "planavimo rūšis"])
        combined = f"{type_raw} {title}".lower()

        for keyword, doc_type in DOC_TYPE_MAP.items():
            if keyword in combined:
                return doc_type

        return "special"

    def _get_field(self, info: dict, keys: list[str]) -> str:
        for key in keys:
            for k, v in info.items():
                if key in k:
                    return v
        return ""

    def _parse_date(self, text: str) -> str | None:
        """Try to parse a Lithuanian date string into ISO format."""
        if not text:
            return None
        text = text.strip()

        for fmt in ["%Y-%m-%d", "%Y.%m.%d", "%d.%m.%Y", "%d-%m-%Y", "%Y/%m/%d"]:
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue

        return None
