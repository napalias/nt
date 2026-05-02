"""Aruodas.lt spider — Lithuania's largest real estate portal.

Usage:
    scrapy crawl aruodas
    scrapy crawl aruodas -a max_pages=2            # debug: limit pages
    scrapy crawl aruodas -a property_type=namai     # houses only
    scrapy crawl aruodas -a property_type=butai     # flats only

IMPORTANT: Aruodas uses Cloudflare protection.
All requests go through scrapy-playwright (chromium) with networkidle waits.
DOWNLOAD_DELAY is set to 5s to avoid triggering rate limits.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import scrapy
from scrapy.http import HtmlResponse

from realestate_spiders.items import ListingItem

CATEGORY_PATHS: dict[str, str] = {
    "namai": "/namai/puslapis/{page}/",
    "butai": "/butai/puslapis/{page}/",
}

PROPERTY_TYPE_MAP: dict[str, str] = {
    "namai": "house",
    "butai": "flat",
}

BUILDING_TYPE_MAP: dict[str, str] = {
    "mūrinis": "brick",
    "blokinis": "block",
    "medinis": "wooden",
    "monolitinis": "monolithic",
    "rąstinis": "log",
    "karkasinis": "frame",
    "kita": "other",
}

# Rotating user-agent pool — Cloudflare fingerprints on UA + TLS, so we rotate
# across recent desktop Chrome versions.
USER_AGENTS: list[str] = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0"),
]


class AruodasSpider(scrapy.Spider):
    """Crawls aruodas.lt house and flat listings for sale.

    All requests use scrapy-playwright to bypass Cloudflare protection.
    """

    name = "aruodas"
    allowed_domains = ["aruodas.lt", "www.aruodas.lt"]

    custom_settings: dict = {
        "DOWNLOAD_DELAY": 5,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "ROBOTSTXT_OBEY": False,  # Aruodas robots.txt blocks scrapers; we throttle politely
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_LAUNCH_OPTIONS": {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        },
        "PLAYWRIGHT_CONTEXTS": {
            "default": {
                "locale": "lt-LT",
                "timezone_id": "Europe/Vilnius",
                "viewport": {"width": 1920, "height": 1080},
            },
        },
    }

    def __init__(
        self,
        max_pages: int = 0,
        property_type: str = "namai",
        *args,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.max_pages = int(max_pages)
        self.property_type = property_type
        self.current_page = 1
        self._ua_index = 0

    def _next_user_agent(self) -> str:
        """Return the next user-agent from the rotation pool."""
        ua = USER_AGENTS[self._ua_index % len(USER_AGENTS)]
        self._ua_index += 1
        return ua

    def _playwright_meta(self) -> dict:
        """Common meta dict for all playwright requests."""
        return {
            "playwright": True,
            "playwright_include_page": False,
            "playwright_context": "default",
            "playwright_page_goto_kwargs": {
                "wait_until": "networkidle",
                "timeout": 60_000,
            },
        }

    def start_requests(self):
        path_template = CATEGORY_PATHS.get(self.property_type)
        if not path_template:
            self.logger.error(
                "Unknown property_type=%s. Valid: %s",
                self.property_type,
                ", ".join(CATEGORY_PATHS),
            )
            return

        url = "https://www.aruodas.lt" + path_template.format(page=1)
        yield scrapy.Request(
            url,
            callback=self.parse_index,
            headers={"User-Agent": self._next_user_agent()},
            meta=self._playwright_meta(),
            dont_filter=True,
        )

    # ------------------------------------------------------------------
    # Index page
    # ------------------------------------------------------------------

    def parse_index(self, response: HtmlResponse):
        """Extract listing links from the search results page."""
        # Aruodas listing rows live in a table; each row links to a detail page.
        # Detail URLs follow the pattern: /namai/.../-N-XXXXX/ or /butai/.../-N-XXXXX/
        listing_links: list[str] = []

        # Primary: tr.list-row links
        for row in response.css("tr.list-row"):
            link = row.css("td.list-adress a::attr(href)").get()
            if not link:
                link = row.css("a[href*='aruodas.lt']::attr(href)").get()
            if not link:
                link = row.css("a::attr(href)").get()
            if link and re.search(r"-\d+-\d+/$", link):
                listing_links.append(link)

        # Fallback: any anchor matching the detail URL pattern
        if not listing_links:
            all_links = response.css("a::attr(href)").getall()
            for link in all_links:
                if re.search(r"aruodas\.lt/.+/-\d+-\d+/?$", link):
                    listing_links.append(link)

        # Deduplicate while preserving order
        listing_links = list(dict.fromkeys(listing_links))

        self.logger.info(
            "Page %d: found %d listing links",
            self.current_page,
            len(listing_links),
        )

        if not listing_links:
            self.logger.info("No listings found on page %d, stopping", self.current_page)
            return

        for link in listing_links:
            url = response.urljoin(link)
            yield scrapy.Request(
                url,
                callback=self.parse_detail,
                headers={"User-Agent": self._next_user_agent()},
                meta=self._playwright_meta(),
            )

        # Next page
        self.current_page += 1
        if self.max_pages and self.current_page > self.max_pages:
            self.logger.info("Reached max_pages=%d, stopping", self.max_pages)
            return

        path_template = CATEGORY_PATHS[self.property_type]
        next_url = "https://www.aruodas.lt" + path_template.format(page=self.current_page)
        yield scrapy.Request(
            next_url,
            callback=self.parse_index,
            headers={"User-Agent": self._next_user_agent()},
            meta=self._playwright_meta(),
            dont_filter=True,
        )

    # ------------------------------------------------------------------
    # Detail page
    # ------------------------------------------------------------------

    def parse_detail(self, response: HtmlResponse):
        """Parse a single listing detail page and yield a ListingItem."""
        # --- Title ---
        title = response.css("h1.obj-header-text::text").get("").strip()
        if not title:
            title = response.css("h1::text").get("").strip()

        # --- Price ---
        price_text = self._extract_price(response)

        # --- Parameters table ---
        # Aruodas uses a dt/dd structure inside .obj-details or a table
        info_table = self._extract_info_table(response)

        # --- Description ---
        description = self._extract_description(response)

        # --- Photos ---
        photos = self._extract_photos(response)

        # --- Location ---
        address = self._get_field(info_table, ["adresas", "vieta", "vietovė"])
        city = self._get_field(info_table, ["miestas"])
        municipality = self._get_field(info_table, ["savivaldybė"])
        district = self._get_field(info_table, ["mikrorajonas", "rajonas"])

        # Fallback: address from breadcrumbs
        if not address:
            breadcrumbs = response.css(
                ".obj-header-text-left .obj-header-region::text, .breadcrumb a::text"
            ).getall()
            if breadcrumbs:
                address = ", ".join(b.strip() for b in breadcrumbs if b.strip())

        # --- Floor ---
        floor_text = self._get_field(info_table, ["aukštas", "floor"])
        floor_val, total_floors_val = self._parse_floor(floor_text)

        total_floors_direct = self._get_field(info_table, ["aukštų sk.", "aukštų skaičius"])
        if total_floors_direct:
            total_floors_val = total_floors_direct

        # --- Building type ---
        building_type_raw = self._get_field(
            info_table, ["namo tipas", "pastato tipas", "statybos tipas"]
        )
        building_type = BUILDING_TYPE_MAP.get(
            building_type_raw.lower() if building_type_raw else "", ""
        )

        # --- Heating & energy ---
        heating_raw = self._get_field(info_table, ["šildymas", "heating"])
        energy_raw = self._get_field(
            info_table, ["pastato energinio naudingumo klasė", "energijos klasė", "energy"]
        )

        # --- New construction detection ---
        search_text = (title + " " + description).lower()
        is_new = any(
            kw in search_text
            for kw in ["naujos statybos", "naujas namas", "nauja statyba", "naujai pastatytas"]
        )

        # --- Year built ---
        year_text = self._get_field(info_table, ["statybos metai", "metai", "year"])

        # --- Source ID ---
        # Aruodas URLs end with pattern like -N-XXXXX/ e.g. -1-3314435/
        source_id = self._extract_source_id(response.url)

        # --- Coordinates ---
        latitude, longitude = self._extract_coordinates(response)

        yield ListingItem(
            url=response.url,
            source="aruodas",
            source_id=source_id,
            title=title,
            description=description,
            price=price_text,
            property_type=PROPERTY_TYPE_MAP.get(self.property_type, "house"),
            listing_type="sale",
            area_sqm=self._get_field(
                info_table, ["plotas", "bendras plotas", "buto plotas", "namo plotas"]
            ),
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
            municipality=municipality or "",
            district=district or "",
            latitude=latitude,
            longitude=longitude,
            photo_urls=photos,
            raw_data=info_table,
            scraped_at=datetime.now(UTC).isoformat(),
        )

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    def _extract_price(self, response: HtmlResponse) -> str:
        """Extract the price text from the detail page."""
        # Primary: the main price element
        price = response.css(".price-eur span::text").get("")
        if not price:
            price = response.css(".price-eur::text").get("")
        if not price:
            price = response.css("[class*='price'] .price-eur::text").get("")
        if not price:
            # Fallback: any element with price-like content
            price = response.css(".obj-price .price-eur::text").get("")
        return price.strip()

    def _extract_info_table(self, response: HtmlResponse) -> dict[str, str]:
        """Extract key-value pairs from the property details section."""
        info: dict[str, str] = {}

        # Aruodas uses dt/dd pairs inside .obj-details
        for dt in response.css(".obj-details dt"):
            key = dt.css("::text").get("").strip().rstrip(":").lower()
            dd = dt.xpath("following-sibling::dd[1]")
            value = dd.css("::text").get("").strip()
            if key and value:
                info[key] = value

        # Fallback: table rows
        if not info:
            for row in response.css("table.obj-details tr, .obj-details tr"):
                key = row.css("td:first-child::text, th::text").get("").strip().rstrip(":")
                value = row.css("td:last-child::text, td:nth-child(2)::text").get("").strip()
                if key and value:
                    info[key.lower()] = value

        # Second fallback: div-based layout (Aruodas sometimes uses this)
        if not info:
            for row in response.css(".obj-details .obj-detail-line, .obj-details li"):
                key = row.css(".obj-detail-title::text, .title::text").get("").strip().rstrip(":")
                value = row.css(".obj-detail-value::text, .value::text").get("").strip()
                if key and value:
                    info[key.lower()] = value

        return info

    def _extract_description(self, response: HtmlResponse) -> str:
        """Extract the listing description text."""
        # Primary: the comment/description block
        desc_parts = response.css(
            "#collapsedText::text, #collapsedTextBlock::text, "
            ".obj-comment::text, .obj-comment p::text"
        ).getall()

        if not desc_parts:
            desc_parts = response.css(
                "[id*='collapsedText']::text, .description::text, .description p::text"
            ).getall()

        return " ".join(part.strip() for part in desc_parts if part.strip())

    def _extract_photos(self, response: HtmlResponse) -> list[str]:
        """Extract photo URLs from the gallery."""
        photos: list[str] = []

        # Aruodas gallery images (full-size links or large thumbnails)
        gallery_imgs = response.css(
            ".obj-images img::attr(src), "
            ".obj-gallery img::attr(src), "
            ".gallery-list img::attr(src), "
            "[class*='gallery'] img::attr(data-src), "
            "[class*='gallery'] img::attr(src)"
        ).getall()
        photos.extend(gallery_imgs)

        # Also check for data-url or data-original attributes (lazy-loaded)
        lazy_imgs = response.css(
            ".obj-images img::attr(data-original), "
            ".obj-images img::attr(data-src), "
            ".obj-gallery img::attr(data-original)"
        ).getall()
        photos.extend(lazy_imgs)

        # Deduplicate, filter thumbnails, keep only full URLs
        seen: set[str] = set()
        clean: list[str] = []
        for url in photos:
            if not url or not url.startswith("http"):
                continue
            # Skip tiny thumbnails
            if "/thumb_" in url or "/small_" in url:
                continue
            if url not in seen:
                seen.add(url)
                clean.append(url)

        return clean

    def _extract_source_id(self, url: str) -> str:
        """Extract the listing ID from an aruodas URL.

        URL pattern: https://www.aruodas.lt/namai/vilniuje/...-N-XXXXX/
        We extract 'N-XXXXX' as the source_id.
        """
        match = re.search(r"-(\d+-\d+)/?$", url)
        if match:
            return match.group(1)
        # Fallback: just the last numeric segment
        match = re.search(r"-(\d+)/?$", url)
        if match:
            return match.group(1)
        return ""

    def _extract_coordinates(self, response: HtmlResponse) -> tuple[str, str]:
        """Try to extract lat/lng from the embedded map on the page."""
        # Aruodas embeds coordinates in JavaScript for the map
        body_text = response.text

        # Pattern: coordinates in JS variable or data attribute
        lat_match = re.search(r'"lat(?:itude)?"\s*:\s*([0-9]+\.[0-9]+)', body_text)
        lng_match = re.search(r'"lng|lon(?:gitude)?"\s*:\s*([0-9]+\.[0-9]+)', body_text)

        if lat_match and lng_match:
            return lat_match.group(1), lng_match.group(1)

        # Try data attributes on map element
        lat = response.css("[data-lat]::attr(data-lat)").get("")
        lng = response.css("[data-lng]::attr(data-lng), [data-lon]::attr(data-lon)").get("")
        if lat and lng:
            return lat, lng

        # Try map-related script content for LatLng constructor
        map_match = re.search(
            r"(?:LatLng|latlng)\s*\(\s*([0-9]+\.[0-9]+)\s*,\s*([0-9]+\.[0-9]+)\s*\)",
            body_text,
        )
        if map_match:
            return map_match.group(1), map_match.group(2)

        return "", ""

    def _get_field(self, info: dict[str, str], keys: list[str]) -> str:
        """Look up a value in the info dict by trying multiple key substrings."""
        for key in keys:
            for k, v in info.items():
                if key in k:
                    return v
        return ""

    def _parse_floor(self, text: str) -> tuple[str, str]:
        """Parse floor text like '3/5' into (floor, total_floors)."""
        if not text:
            return "", ""
        match = re.match(r"(\d+)\s*/\s*(\d+)", text)
        if match:
            return match.group(1), match.group(2)
        match = re.match(r"(\d+)", text)
        if match:
            return match.group(1), ""
        return "", ""
