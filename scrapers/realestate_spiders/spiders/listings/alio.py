"""Alio.lt spider — Lithuanian classifieds, real estate section.

Usage:
    scrapy crawl alio
    scrapy crawl alio -a max_pages=2
    scrapy crawl alio -a property_type=namai -a region=kretinga
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import scrapy
from scrapy.http import HtmlResponse

from realestate_spiders.items import ListingItem

CATEGORY_PATHS = {
    "namai": "/nekilnojamas-turtas/namai-kotedzai",
    "butai": "/nekilnojamas-turtas/butai",
    "sklypai": "/nekilnojamas-turtas/sklypai",
}

REGION_PATHS = {
    "kretinga": "/kretingos-r-sav",
    "palanga": "/palangos-m-sav",
    "klaipeda": "/klaipedos-m-sav",
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


class AlioSpider(scrapy.Spider):
    name = "alio"
    allowed_domains = ["alio.lt", "www.alio.lt"]

    custom_settings = {
        "DOWNLOAD_DELAY": 3,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
    }

    def __init__(
        self, max_pages: int = 0, property_type: str = "namai", region: str = "", *args, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.max_pages = int(max_pages)
        self.property_type = property_type
        self.region = region
        self.current_page = 1

    def _pw_meta(self):
        return {
            "playwright": True,
            "playwright_include_page": False,
            "playwright_page_goto_kwargs": {"wait_until": "networkidle"},
        }

    def start_requests(self):
        base = CATEGORY_PATHS.get(self.property_type)
        if not base:
            self.logger.error("Unknown property_type: %s", self.property_type)
            return

        path = f"https://www.alio.lt{base}"
        if self.region and self.region in REGION_PATHS:
            path += REGION_PATHS[self.region]
        path += f"/page/{self.current_page}.html"

        yield scrapy.Request(path, callback=self.parse_index, meta=self._pw_meta())

    def parse_index(self, response: HtmlResponse):
        links = response.css("a::attr(href)").getall()
        detail_links = [
            lnk
            for lnk in links
            if re.search(r"/nekilnojamas-turtas/.*-\d+\.html", lnk)
            and "/page/" not in lnk
        ]
        detail_links = list(dict.fromkeys(detail_links))

        self.logger.info("Page %d: found %d listing links", self.current_page, len(detail_links))

        if not detail_links:
            self.logger.info("No listings on page %d, stopping", self.current_page)
            return

        for link in detail_links:
            url = response.urljoin(link)
            yield scrapy.Request(url, callback=self.parse_detail, meta=self._pw_meta())

        self.current_page += 1
        if self.max_pages and self.current_page > self.max_pages:
            self.logger.info("Reached max_pages=%d, stopping", self.max_pages)
            return

        base = CATEGORY_PATHS[self.property_type]
        path = f"https://www.alio.lt{base}"
        if self.region and self.region in REGION_PATHS:
            path += REGION_PATHS[self.region]
        path += f"/page/{self.current_page}.html"

        yield scrapy.Request(path, callback=self.parse_index, meta=self._pw_meta())

    def parse_detail(self, response: HtmlResponse):
        title = response.css("h1::text").get("").strip()
        if not title:
            title = response.css("title::text").get("").strip()

        all_text = [t.strip() for t in response.css("body *::text").getall() if t.strip()]

        price = self._extract_price(all_text)
        info = self._extract_info(response, all_text)

        area = info.get("area", "")
        plot = info.get("plot", "")
        rooms = info.get("rooms", "")
        year = info.get("year", "")
        building_type = BUILDING_TYPE_MAP.get(info.get("building_type", "").lower(), "")
        heating = info.get("heating", "")
        energy = info.get("energy", "")
        address = info.get("address", "") or self._address_from_title(title)
        city = info.get("city", "") or self._extract_city(title, address)

        description = " ".join(
            t for t in all_text if len(t) > 40 and not t.startswith("http")
        )[:2000]

        photos = self._extract_photos(response)

        is_new = any(
            kw in (title + " " + description).lower()
            for kw in ["naujos statybos", "naujas namas", "nauja statyba", "2025 m.", "2026 m."]
        )

        source_id_match = re.search(r"-(\d+)\.html", response.url)
        source_id = source_id_match.group(1) if source_id_match else ""

        yield ListingItem(
            url=response.url,
            source="alio",
            source_id=source_id,
            title=title,
            description=description,
            price=price,
            property_type=PROPERTY_TYPE_MAP.get(self.property_type, "house"),
            listing_type="sale",
            area_sqm=area,
            plot_area_ares=plot,
            rooms=rooms,
            year_built=year,
            building_type=building_type,
            heating_type=heating,
            energy_class=energy,
            is_new_construction=is_new,
            address=address,
            city=city,
            photo_urls=photos,
            raw_data=info,
            scraped_at=datetime.now(UTC).isoformat(),
        )

    def _extract_price(self, texts: list[str]) -> str:
        for t in texts:
            m = re.search(r"([\d\s]+)\s*€", t)
            if m:
                return m.group(0).strip()
        return ""

    def _extract_info(self, response: HtmlResponse, texts: list[str]) -> dict:
        info: dict[str, str] = {}

        for sel in ["table tr", "dl dt", ".params div", ".details div", ".info-row"]:
            rows = response.css(sel)
            for row in rows:
                parts = [t.strip() for t in row.css("::text").getall() if t.strip()]
                if len(parts) >= 2:
                    key = parts[0].rstrip(":").lower()
                    val = parts[-1]
                    if key != val.lower():
                        info[key] = val

        field_map = {
            "area": ["plotas", "bendras plotas", "namo plotas"],
            "plot": ["sklypo plotas", "sklypas", "žemės plotas"],
            "rooms": ["kambarių", "kambariai"],
            "year": ["statybos metai", "metai"],
            "building_type": ["namo tipas", "pastato tipas"],
            "heating": ["šildymas"],
            "energy": ["energijos klasė"],
            "address": ["adresas", "vieta"],
            "city": ["miestas"],
        }

        result: dict[str, str] = {}
        for field, keys in field_map.items():
            for k in keys:
                for ik, iv in info.items():
                    if k in ik:
                        result[field] = iv
                        break
                if field in result:
                    break

        for t in texts:
            tl = t.lower()
            if "area" not in result:
                m = re.search(r"(\d+[\.,]?\d*)\s*(?:kv\.?\s*m|m²)", tl)
                if m:
                    result["area"] = m.group(1).replace(",", ".")
            if "rooms" not in result:
                m = re.search(r"(\d+)\s*kamb", tl)
                if m:
                    result["rooms"] = m.group(1)
            if "year" not in result:
                m = re.search(r"(\d{4})\s*m\.", tl)
                if m:
                    result["year"] = m.group(1)

        return result

    def _extract_photos(self, response: HtmlResponse) -> list[str]:
        photos: list[str] = []
        for sel in [
            "img[src*='/photos/']::attr(src)",
            "img[src*='/images/']::attr(src)",
            "img[data-src]::attr(data-src)",
            ".gallery img::attr(src)",
            ".photo img::attr(src)",
            "meta[property='og:image']::attr(content)",
        ]:
            photos.extend(response.css(sel).getall())

        return list(dict.fromkeys(
            p for p in photos
            if p.startswith("http") and "logo" not in p and "icon" not in p
        ))

    def _address_from_title(self, title: str) -> str:
        parts = re.split(r"\b(?:namas|butas|kotedžas|sklypas|sodyba)\b", title, flags=re.I)
        if len(parts) > 1:
            return parts[-1].strip().strip(",").strip()
        return title

    def _extract_city(self, title: str, address: str) -> str:
        text = f"{title} {address}".lower()
        cities = [
            "Kretinga", "Palanga", "Klaipėda", "Vilnius", "Kaunas",
            "Šiauliai", "Panevėžys", "Alytus", "Marijampolė",
        ]
        for city in cities:
            if city.lower() in text:
                return city
        return ""
