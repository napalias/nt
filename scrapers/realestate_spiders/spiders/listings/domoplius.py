"""Domoplius.lt spider — walks index pages, parses listing detail pages.

Usage:
    scrapy crawl domoplius
    scrapy crawl domoplius -a max_pages=2          # debug: limit pages
    scrapy crawl domoplius -a property_type=namai   # houses only
    scrapy crawl domoplius -a property_type=butai   # flats only

Uses playwright for index pages (JS-rendered). Detail pages use plain requests.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import scrapy
from scrapy.http import HtmlResponse

from realestate_spiders.items import ListingItem

CATEGORY_PATHS = {
    "namai": "/skelbimai/namai-kotedzai-sodai?action_type=1&page_nr={page}",
    "butai": "/skelbimai/butai?action_type=1&page_nr={page}",
    "sklypai": "/skelbimai/sklypai?action_type=1&page_nr={page}",
}

PROPERTY_TYPE_MAP = {
    "namai": "house",
    "butai": "flat",
    "sklypai": "plot",
}

BUILDING_TYPE_MAP = {
    "mūrinis": "brick",
    "blokinis": "block",
    "medinis": "wooden",
    "monolitinis": "monolithic",
    "rąstinis": "log",
    "karkasinis": "frame",
}


class DomopliusSpider(scrapy.Spider):
    name = "domoplius"
    allowed_domains = ["domoplius.lt"]

    custom_settings = {
        "DOWNLOAD_DELAY": 3,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
    }

    def __init__(self, max_pages: int = 0, property_type: str = "namai", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_pages = int(max_pages)
        self.property_type = property_type
        self.current_page = 1

    def start_requests(self):
        path_template = CATEGORY_PATHS.get(self.property_type)
        if not path_template:
            self.logger.error("Unknown property_type: %s", self.property_type)
            return

        url = "https://domoplius.lt" + path_template.format(page=1)
        yield scrapy.Request(
            url,
            callback=self.parse_index,
            meta={
                "playwright": True,
                "playwright_include_page": False,
                "playwright_page_goto_kwargs": {"wait_until": "networkidle"},
            },
        )

    def parse_index(self, response: HtmlResponse):
        listing_links = response.css("a[href*='/skelbimai/']::attr(href)").getall()
        detail_links = [link for link in listing_links if re.search(r"-\d+\.html$", link)]
        detail_links = list(dict.fromkeys(detail_links))

        self.logger.info("Page %d: found %d listing links", self.current_page, len(detail_links))

        if not detail_links:
            self.logger.info("No listings found on page %d, stopping", self.current_page)
            return

        for link in detail_links:
            url = response.urljoin(link)
            yield scrapy.Request(url, callback=self.parse_detail)

        self.current_page += 1
        if self.max_pages and self.current_page > self.max_pages:
            self.logger.info("Reached max_pages=%d, stopping", self.max_pages)
            return

        path_template = CATEGORY_PATHS[self.property_type]
        next_url = "https://domoplius.lt" + path_template.format(page=self.current_page)
        yield scrapy.Request(
            next_url,
            callback=self.parse_index,
            meta={
                "playwright": True,
                "playwright_include_page": False,
                "playwright_page_goto_kwargs": {"wait_until": "networkidle"},
            },
        )

    def parse_detail(self, response: HtmlResponse):
        title = response.css("h1::text").get("").strip()
        if not title:
            title = response.css("title::text").get("").strip()

        price_text = (
            response.css(".price .h1::text").get("")
            or response.css("[class*='price'] .h1::text").get("")
            or response.css("[class*='price']::text").get("")
        )

        info_table = {}
        for row in response.css("table.view-group tr, .obj-details tr, .group-data tr"):
            key = row.css("th::text, td:first-child::text").get("").strip().lower()
            value = row.css("td:last-child::text, td:nth-child(2)::text").get("").strip()
            if key and value:
                info_table[key] = value

        if not info_table:
            for row in response.css("dl dt, .info-block .info-row"):
                key_el = row.css("dt::text, .label::text, span:first-child::text")
                key = key_el.get("").strip().lower()
                value = row.css("dd::text, .value::text, span:last-child::text").get("")
                if not value:
                    sibling = row.xpath("following-sibling::dd[1]/text()").get("")
                    value = sibling.strip() if sibling else ""
                if key and value:
                    info_table[key] = value.strip()

        description = " ".join(
            response.css(
                ".description::text, .obj-comment::text, [class*='description'] p::text"
            ).getall()
        ).strip()

        photos = response.css(
            "img[src*='img.domoplius'], img[src*='images.domoplius'], "
            ".photo-gallery img::attr(src), "
            "[class*='gallery'] img::attr(src), "
            ".carousel img::attr(src)"
        ).getall()
        photos = [p for p in photos if "thumb" not in p and p.startswith("http")]

        address = self._get_field(info_table, ["adresas", "vieta", "vietovė", "address"])
        city = self._get_field(info_table, ["miestas", "city"])
        district = self._get_field(info_table, ["rajonas", "mikrorajonas", "district"])

        if not address:
            breadcrumbs = response.css(".breadcrumbs a::text, .breadcrumb a::text").getall()
            if breadcrumbs:
                address = ", ".join(b.strip() for b in breadcrumbs if b.strip())

        floor_text = self._get_field(info_table, ["aukštas", "floor"])
        floor_val, total_floors_val = self._parse_floor(floor_text)

        total_floors_direct = self._get_field(info_table, ["aukštų sk.", "aukštų skaičius"])
        if total_floors_direct:
            total_floors_val = total_floors_direct

        building_type_raw = self._get_field(
            info_table, ["namo tipas", "pastato tipas", "statybos tipas", "tipas"]
        )
        building_type = BUILDING_TYPE_MAP.get(
            building_type_raw.lower() if building_type_raw else "", ""
        )

        heating_raw = self._get_field(info_table, ["šildymas", "heating"])
        energy_raw = self._get_field(info_table, ["energijos klasė", "energy"])

        is_new = any(
            kw in (title + " " + description).lower()
            for kw in ["naujos statybos", "naujas namas", "nauja statyba"]
        )

        year_text = self._get_field(info_table, ["statybos metai", "metai", "year"])

        source_id_match = re.search(r"-(\d+)\.html", response.url)
        source_id = source_id_match.group(1) if source_id_match else ""

        yield ListingItem(
            url=response.url,
            source="domoplius",
            source_id=source_id,
            title=title,
            description=description,
            price=price_text,
            property_type=PROPERTY_TYPE_MAP.get(self.property_type, "house"),
            listing_type="sale",
            area_sqm=self._get_field(info_table, ["plotas", "bendras plotas", "area"]),
            plot_area_ares=self._get_field(info_table, ["sklypo plotas", "sklypas"]),
            rooms=self._get_field(info_table, ["kambarių sk.", "kambariai", "rooms"]),
            floor=floor_val,
            total_floors=total_floors_val,
            year_built=year_text,
            building_type=building_type,
            heating_type=heating_raw or "",
            energy_class=energy_raw or "",
            is_new_construction=is_new,
            address=address or "",
            city=city or "",
            district=district or "",
            photo_urls=photos,
            raw_data=info_table,
            scraped_at=datetime.now(UTC).isoformat(),
        )

    def _get_field(self, info: dict, keys: list[str]) -> str:
        for key in keys:
            for k, v in info.items():
                if key in k:
                    return v
        return ""

    def _parse_floor(self, text: str) -> tuple[str, str]:
        if not text:
            return "", ""
        match = re.match(r"(\d+)\s*/\s*(\d+)", text)
        if match:
            return match.group(1), match.group(2)
        match = re.match(r"(\d+)", text)
        if match:
            return match.group(1), ""
        return "", ""
