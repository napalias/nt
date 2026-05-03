"""Skelbiu.lt spider — walks index pages, parses listing detail pages.

Usage:
    scrapy crawl skelbiu
    scrapy crawl skelbiu -a max_pages=2          # debug: limit pages
    scrapy crawl skelbiu -a property_type=namai   # houses only
    scrapy crawl skelbiu -a property_type=butai   # flats only

Uses playwright for index pages (JS-rendered). Detail pages use plain requests.
Skelbiu.lt is behind Cloudflare, so playwright is required for all pages.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import scrapy
from scrapy.http import HtmlResponse

from realestate_spiders.items import ListingItem

# Skelbiu.lt category paths for sale listings.
# The site uses query parameters for filtering; pagination is via ?page=N.
CATEGORY_URLS = {
    "namai": (
        "https://www.skelbiu.lt/skelbimai/nekilnojamasis-turtas/namai-kotedzai-sodai/?page={page}"
    ),
    "butai": ("https://www.skelbiu.lt/skelbimai/nekilnojamasis-turtas/butai/?page={page}"),
    "sklypai": ("https://www.skelbiu.lt/skelbimai/nekilnojamasis-turtas/sklypai/?page={page}"),
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
    "mūrinis/blokinis": "brick",
    "kita": "",
}

# Known Lithuanian field name variants on skelbiu.lt detail pages.
# Multiple fallback keys per concept to be robust against structure changes.
FIELD_ALIASES = {
    "area": ["plotas", "bendras plotas", "buto plotas", "namo plotas", "gyv. plotas"],
    "plot_area": ["sklypo plotas", "žemės plotas", "sklypas"],
    "rooms": ["kambarių sk.", "kambariai", "kambarių skaičius"],
    "floor": ["aukštas", "aukštas / aukštų sk."],
    "total_floors": ["aukštų sk.", "aukštų skaičius", "aukštingumas"],
    "year_built": ["statybos metai", "metai", "pastatymo metai"],
    "building_type": [
        "namo tipas",
        "pastato tipas",
        "statybos tipas",
        "tipas",
        "konstrukcija",
    ],
    "heating": ["šildymas", "šildymo tipas"],
    "energy_class": ["energijos klasė", "energetinė klasė", "energijos suvartojimo klasė"],
    "address": ["adresas", "vieta", "vietovė"],
    "city": ["miestas", "gyvenvietė"],
    "district": ["rajonas", "mikrorajonas", "savivaldybė"],
}


class SkelbiuSpider(scrapy.Spider):
    name = "skelbiu"
    allowed_domains = ["skelbiu.lt", "www.skelbiu.lt"]

    custom_settings = {
        "DOWNLOAD_DELAY": 3,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
    }

    def __init__(self, max_pages: int = 0, property_type: str = "namai", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_pages = int(max_pages)
        self.property_type = property_type
        self.current_page = 1

    # ------------------------------------------------------------------
    # Index pages
    # ------------------------------------------------------------------

    def start_requests(self):
        url_template = CATEGORY_URLS.get(self.property_type)
        if not url_template:
            self.logger.error("Unknown property_type: %s", self.property_type)
            return

        url = url_template.format(page=1)
        yield scrapy.Request(
            url,
            callback=self.parse_index,
            meta=self._playwright_meta(),
        )

    def parse_index(self, response: HtmlResponse):
        # Skelbiu.lt listing links follow patterns like:
        #   /skelbimai/<slug>-<id>.html
        #   /skelbimai/<category>/<slug>-<id>.html
        # We look for links containing a numeric ID before .html
        listing_links = self._extract_listing_links(response)
        listing_links = list(dict.fromkeys(listing_links))  # deduplicate, preserve order

        self.logger.info("Page %d: found %d listing links", self.current_page, len(listing_links))

        if not listing_links:
            self.logger.info("No listings found on page %d, stopping", self.current_page)
            return

        for link in listing_links:
            url = response.urljoin(link)
            # Detail pages also need playwright on skelbiu.lt (Cloudflare)
            yield scrapy.Request(
                url,
                callback=self.parse_detail,
                meta=self._playwright_meta(),
            )

        # Pagination
        self.current_page += 1
        if self.max_pages and self.current_page > self.max_pages:
            self.logger.info("Reached max_pages=%d, stopping", self.max_pages)
            return

        url_template = CATEGORY_URLS[self.property_type]
        next_url = url_template.format(page=self.current_page)
        yield scrapy.Request(
            next_url,
            callback=self.parse_index,
            meta=self._playwright_meta(),
        )

    def _extract_listing_links(self, response: HtmlResponse) -> list[str]:
        """Extract detail page links from the index page using multiple strategies."""
        links: list[str] = []

        # Strategy 1: Links under the real estate category path
        all_links = response.css("a::attr(href)").getall()
        for link in all_links:
            if "/nekilnojamasis-turtas/" in link and re.search(r"-\d+\.html", link):
                links.append(link)

        # Strategy 1b: Any link with RE keywords in the slug
        if not links:
            re_slugs = (
                "namas", "butas", "sklypas", "kotedzas", "sodyba",
                "namai", "butai", "sklypai",
            )
            for link in all_links:
                if re.search(r"/skelbimai/.*-\d+\.html", link):
                    slug = link.rsplit("/", 1)[-1].lower()
                    if any(kw in slug for kw in re_slugs):
                        links.append(link)

        if links:
            return links

        # Strategy 2: Links inside known listing container classes
        for selector in [
            ".standard-list-container a::attr(href)",
            ".list-search-result a::attr(href)",
            ".search-list a::attr(href)",
            ".skelbimai-list a::attr(href)",
            ".classifieds a::attr(href)",
            "[data-item-id] a::attr(href)",
            ".item-list a::attr(href)",
        ]:
            found = response.css(selector).getall()
            for link in found:
                if link and ".html" in link and "/skelbimai/" in link:
                    links.append(link)
            if links:
                return links

        # Strategy 3: XPath for any link whose href ends with digits + .html
        xpath_links = response.xpath(
            "//a[re:test(@href, '/skelbimai/.*-\\d+\\.html')]/@href",
            namespaces={"re": "http://exslt.org/regular-expressions"},
        ).getall()
        links.extend(xpath_links)

        return links

    # ------------------------------------------------------------------
    # Detail pages
    # ------------------------------------------------------------------

    def parse_detail(self, response: HtmlResponse):
        title = self._extract_title(response)
        price_text = self._extract_price(response)
        info_table = self._extract_info_table(response)
        description = self._extract_description(response)
        photos = self._extract_photos(response)

        # Location fields
        address = self._get_field(info_table, FIELD_ALIASES["address"])
        city = self._get_field(info_table, FIELD_ALIASES["city"])
        district = self._get_field(info_table, FIELD_ALIASES["district"])

        # Fallback: try breadcrumbs for location
        if not address:
            address = self._extract_address_from_breadcrumbs(response)

        # Floor parsing — may come as "3/5" or "3 iš 5"
        floor_text = self._get_field(info_table, FIELD_ALIASES["floor"])
        floor_val, total_floors_val = self._parse_floor(floor_text)

        # Explicit total floors field overrides parsed value
        total_floors_direct = self._get_field(info_table, FIELD_ALIASES["total_floors"])
        if total_floors_direct:
            total_floors_val = total_floors_direct

        # Building type
        building_type_raw = self._get_field(info_table, FIELD_ALIASES["building_type"])
        building_type = BUILDING_TYPE_MAP.get(
            building_type_raw.lower() if building_type_raw else "", ""
        )

        # Other details
        heating_raw = self._get_field(info_table, FIELD_ALIASES["heating"])
        energy_raw = self._get_field(info_table, FIELD_ALIASES["energy_class"])
        year_text = self._get_field(info_table, FIELD_ALIASES["year_built"])

        # New construction detection
        searchable_text = (title + " " + description).lower()
        is_new = any(
            kw in searchable_text
            for kw in ["naujos statybos", "naujas namas", "nauja statyba", "naujas projektas"]
        )

        # Source ID from URL — skelbiu uses patterns like -12345678.html
        source_id = self._extract_source_id(response.url)

        yield ListingItem(
            url=response.url,
            source="skelbiu",
            source_id=source_id,
            title=title,
            description=description,
            price=price_text,
            property_type=PROPERTY_TYPE_MAP.get(self.property_type, "house"),
            listing_type="sale",
            area_sqm=self._get_field(info_table, FIELD_ALIASES["area"]),
            plot_area_ares=self._get_field(info_table, FIELD_ALIASES["plot_area"]),
            rooms=self._get_field(info_table, FIELD_ALIASES["rooms"]),
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

    # ------------------------------------------------------------------
    # Extraction helpers — multiple fallback selectors for robustness
    # ------------------------------------------------------------------

    def _extract_title(self, response: HtmlResponse) -> str:
        """Extract listing title with fallback selectors."""
        for selector in [
            "h1.detail-title::text",
            "h1.classified-title::text",
            "h1[itemprop='name']::text",
            ".detail-header h1::text",
            ".classified-detail h1::text",
            "h1::text",
        ]:
            title = response.css(selector).get("")
            if title.strip():
                return title.strip()

        # Last resort: page <title>
        page_title = response.css("title::text").get("")
        # Strip " - Skelbiu.lt" suffix if present
        return re.sub(r"\s*[-–|]\s*[Ss]kelbiu\.lt.*$", "", page_title).strip()

    def _extract_price(self, response: HtmlResponse) -> str:
        """Extract price text with fallback selectors."""
        for selector in [
            ".detail-price .price::text",
            ".detail-price::text",
            ".classified-price .price::text",
            ".price-value::text",
            "[itemprop='price']::attr(content)",
            "[itemprop='price']::text",
            ".price .h1::text",
            "[class*='price'] .h1::text",
            "[class*='price']::text",
        ]:
            price = response.css(selector).get("")
            if price.strip():
                return price.strip()
        return ""

    def _extract_info_table(self, response: HtmlResponse) -> dict[str, str]:
        """Extract key-value pairs from the listing detail info section.

        Skelbiu.lt typically uses a table or definition-list style layout for
        listing attributes.  We try several patterns.
        """
        info: dict[str, str] = {}

        # Strategy 1: Standard table rows (th/td or td/td pairs)
        for row in response.css(
            "table.detail-info tr, "
            "table.classified-info tr, "
            "table.view-group tr, "
            ".detail-info-table tr, "
            ".detail-params tr, "
            ".params-table tr, "
            ".obj-details tr"
        ):
            key = row.css("th::text, td:first-child::text").get("").strip().lower()
            value = row.css("td:last-child::text, td:nth-child(2)::text").get("").strip()
            if key and value and key != value:
                info[key] = value

        if info:
            return info

        # Strategy 2: Definition lists (dt/dd)
        dts = response.css(".detail-info dt, .classified-info dt, .params dt, .detail-params dt")
        for dt in dts:
            key = dt.css("::text").get("").strip().lower()
            # dd is typically the next sibling
            dd = dt.xpath("following-sibling::dd[1]")
            value = dd.css("::text").get("").strip() if dd else ""
            if key and value:
                info[key] = value

        if info:
            return info

        # Strategy 3: Generic key-value rows with label/value classes
        for row in response.css(
            ".detail-row, .info-row, .param-row, .detail-line, .classified-param"
        ):
            key = (
                row.css(
                    ".label::text, .param-label::text, .detail-label::text, span:first-child::text"
                )
                .get("")
                .strip()
                .lower()
            )
            value = (
                row.css(
                    ".value::text, .param-value::text, .detail-value::text, span:last-child::text"
                )
                .get("")
                .strip()
            )
            if key and value and key != value:
                info[key] = value

        if info:
            return info

        # Strategy 4: Structured data from JSON-LD or microdata
        for meta in response.css("[itemprop]"):
            prop = meta.attrib.get("itemprop", "").lower()
            content = (meta.attrib.get("content", "") or meta.css("::text").get("")).strip()
            if prop and content:
                info[prop] = content

        return info

    def _extract_description(self, response: HtmlResponse) -> str:
        """Extract listing description text."""
        for selector in [
            ".detail-description::text",
            ".detail-description p::text",
            ".classified-description::text",
            ".description::text",
            ".obj-comment::text",
            "[itemprop='description']::text",
            "[class*='description'] p::text",
            ".detail-text::text",
            ".detail-text p::text",
        ]:
            texts = response.css(selector).getall()
            if texts:
                return " ".join(t.strip() for t in texts if t.strip()).strip()
        return ""

    def _extract_photos(self, response: HtmlResponse) -> list[str]:
        """Extract photo URLs, excluding thumbnails."""
        photos: list[str] = []
        for selector in [
            ".detail-gallery img::attr(src)",
            ".detail-gallery img::attr(data-src)",
            ".classified-gallery img::attr(src)",
            ".photo-gallery img::attr(src)",
            ".photo-gallery img::attr(data-src)",
            "[class*='gallery'] img::attr(src)",
            "[class*='gallery'] img::attr(data-src)",
            ".carousel img::attr(src)",
            ".carousel img::attr(data-src)",
            "img[src*='img.skelbiu']::attr(src)",
            "img[src*='images.skelbiu']::attr(src)",
            "meta[property='og:image']::attr(content)",
        ]:
            found = response.css(selector).getall()
            photos.extend(found)

        # Deduplicate while preserving order, filter out thumbnails and non-http
        seen: set[str] = set()
        clean: list[str] = []
        for url in photos:
            url = url.strip()
            if not url or not url.startswith("http"):
                continue
            if "thumb" in url.lower() or "placeholder" in url.lower():
                continue
            if url not in seen:
                seen.add(url)
                clean.append(url)
        return clean

    def _extract_address_from_breadcrumbs(self, response: HtmlResponse) -> str:
        """Fallback: build an address string from breadcrumb navigation."""
        for selector in [
            ".breadcrumbs a::text",
            ".breadcrumb a::text",
            "[class*='breadcrumb'] a::text",
            "nav.breadcrumb a::text",
        ]:
            crumbs = response.css(selector).getall()
            if crumbs:
                # Filter out generic labels like "Skelbimai", "Nekilnojamasis turtas"
                skip = {"skelbimai", "skelbiu.lt", "nekilnojamasis turtas", "pradinis"}
                parts = [c.strip() for c in crumbs if c.strip() and c.strip().lower() not in skip]
                if parts:
                    return ", ".join(parts)
        return ""

    @staticmethod
    def _extract_source_id(url: str) -> str:
        """Extract the numeric source ID from a skelbiu.lt URL.

        URL patterns:
            /skelbimai/some-title-12345678.html  →  "12345678"
            /skelbimai/nt/title-12345678.html    →  "12345678"
        """
        match = re.search(r"-(\d+)\.html", url)
        return match.group(1) if match else ""

    @staticmethod
    def _get_field(info: dict[str, str], keys: list[str]) -> str:
        """Look up a value in the info dict using multiple possible key names.

        Uses substring matching so "kambarių sk." matches a key like
        "kambarių sk. / miegamieji".
        """
        for key in keys:
            for k, v in info.items():
                if key in k:
                    return v
        return ""

    @staticmethod
    def _parse_floor(text: str) -> tuple[str, str]:
        """Parse floor text that may be 'N', 'N/M', or 'N iš M'."""
        if not text:
            return "", ""
        # "3/5" or "3 / 5"
        match = re.match(r"(\d+)\s*/\s*(\d+)", text)
        if match:
            return match.group(1), match.group(2)
        # "3 iš 5"
        match = re.match(r"(\d+)\s*iš\s*(\d+)", text, re.IGNORECASE)
        if match:
            return match.group(1), match.group(2)
        # Just a number
        match = re.match(r"(\d+)", text)
        if match:
            return match.group(1), ""
        return "", ""

    @staticmethod
    def _playwright_meta() -> dict:
        """Return Scrapy request meta for playwright-rendered pages."""
        return {
            "playwright": True,
            "playwright_include_page": False,
            "playwright_page_goto_kwargs": {"wait_until": "networkidle"},
        }
