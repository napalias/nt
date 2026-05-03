"""Domoplius.lt spider — walks index pages, parses listing detail pages.

Usage:
    scrapy crawl domoplius
    scrapy crawl domoplius -a max_pages=2
    scrapy crawl domoplius -a property_type=namai
    scrapy crawl domoplius -a property_type=butai
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

REGION_BOUNDS = {
    "kretinga": "bounds[north]=56.05&bounds[south]=55.75&bounds[east]=21.55&bounds[west]=20.95",
    "palanga": "bounds[north]=56.0&bounds[south]=55.85&bounds[east]=21.15&bounds[west]=20.9",
    "klaipeda": "bounds[north]=55.78&bounds[south]=55.62&bounds[east]=21.25&bounds[west]=21.05",
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

    def __init__(
        self, max_pages: int = 0, property_type: str = "namai", region: str = "", *args, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.max_pages = int(max_pages)
        self.property_type = property_type
        self.region = region
        self.current_page = 1

    def start_requests(self):
        path_template = CATEGORY_PATHS.get(self.property_type)
        if not path_template:
            self.logger.error("Unknown property_type: %s", self.property_type)
            return

        url = "https://domoplius.lt" + path_template.format(page=1)
        if self.region and self.region in REGION_BOUNDS:
            url += "&" + REGION_BOUNDS[self.region]
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
            yield scrapy.Request(
                url,
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

        path_template = CATEGORY_PATHS[self.property_type]
        next_url = "https://domoplius.lt" + path_template.format(page=self.current_page)
        if self.region and self.region in REGION_BOUNDS:
            next_url += "&" + REGION_BOUNDS[self.region]
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

        all_text = response.css("body::text, body *::text").getall()
        all_text = [t.strip() for t in all_text if t.strip()]

        price_text = self._extract_price(all_text)
        area, plot_area, rooms, year_built = self._extract_summary_stats(all_text)

        info_table = self._extract_info_table(response)

        if not area:
            area = self._get_field(info_table, ["plotas", "bendras plotas", "area"])
        if not rooms:
            rooms = self._get_field(info_table, ["kambarių", "kambariai", "rooms"])
        if not year_built:
            year_built = self._get_field(info_table, ["statybos metai", "metai"])
        if not plot_area:
            plot_area = self._get_field(info_table, ["sklypo plotas", "sklypas"])

        address = self._extract_address(title, response)
        city = self._extract_city(title, address)

        description = self._extract_description(response, all_text)

        # Fallback: extract from description if structured parsing missed them
        desc_lower = (description or "").lower()
        if not area:
            desc_areas = [
                float(m.group(1).replace(",", "."))
                for m in re.finditer(r"(\d+[\.,]?\d*)\s*(?:kv\.?\s*m|m²|m2)", desc_lower)
            ]
            desc_areas = [a for a in desc_areas if 10 < a < 1000]
            if desc_areas:
                area = str(max(desc_areas))
        if not plot_area:
            m = re.search(r"(\d+[\.,]?\d*)\s*(?:a\b|arų|arai|aru)", desc_lower)
            if m:
                plot_area = m.group(1).replace(",", ".")
        if not rooms:
            m = re.search(r"(\d+)\s*(?:kamb|room)", desc_lower)
            if m:
                rooms = m.group(1)
        if not year_built:
            m = re.search(r"(\d{4})\s*(?:m\.|met)", desc_lower)
            if m:
                year_built = m.group(1)

        photos = []
        # Direct img tags
        for sel in [
            "img[src*='domoplius']::attr(src)",
            "img[src*='img.']::attr(src)",
            "img[data-src]::attr(data-src)",
            "img[data-original]::attr(data-original)",
            "img[data-lazy]::attr(data-lazy)",
            ".gallery img::attr(src)",
            ".photo img::attr(src)",
            ".swiper img::attr(src)",
            ".carousel img::attr(src)",
            "picture source::attr(srcset)",
        ]:
            photos.extend(response.css(sel).getall())

        # Background images
        photos.extend(
            response.css("[style*='background-image']").re(
                r'url\(["\']?(https?://[^"\')\s]+)["\']?\)'
            )
        )

        # Filter
        photos = list(
            dict.fromkeys(
                p.split("?")[0]
                for p in photos
                if p.startswith("http")
                and "logo" not in p
                and "icon" not in p
                and ("domoplius" in p or "img." in p or "/photos/" in p or "/images/" in p)
            )
        )

        # og:image fallback
        og_image = response.css('meta[property="og:image"]::attr(content)').get()
        if og_image and og_image not in photos:
            photos.insert(0, og_image)

        building_type_raw = self._get_field(
            info_table, ["namo tipas", "pastato tipas", "tipas"]
        )
        building_type = BUILDING_TYPE_MAP.get(
            building_type_raw.lower() if building_type_raw else "", ""
        )

        heating = self._get_field(info_table, ["šildymas", "heating"])
        energy = self._get_field(info_table, ["energijos klasė", "energy"])

        floor_text = self._get_field(info_table, ["aukštai", "aukštas", "floor"])
        floor_val, total_floors_val = self._parse_floor(floor_text)

        combined = (title + " " + description).lower()
        is_new = any(
            kw in combined
            for kw in [
                "naujos statybos", "naujas namas", "nauja statyba",
                "naujai pastatytas", "naujai statomas", "naujas kotedžas",
                "naujas projektas", "2024 m.", "2025 m.", "2026 m.",
            ]
        )

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
            area_sqm=area,
            plot_area_ares=plot_area,
            rooms=rooms,
            floor=floor_val,
            total_floors=total_floors_val,
            year_built=year_built,
            building_type=building_type,
            heating_type=heating or "",
            energy_class=energy or "",
            is_new_construction=is_new,
            address=address,
            city=city,
            photo_urls=photos,
            raw_data=info_table,
            scraped_at=datetime.now(UTC).isoformat(),
        )

    def _extract_price(self, texts: list[str]) -> str:
        for t in texts:
            match = re.search(r"([\d\s]+)\s*€", t)
            if match:
                return match.group(0).strip()
        return ""

    def _extract_summary_stats(
        self, texts: list[str]
    ) -> tuple[str, str, str, str]:
        area = plot = rooms = year = ""
        all_areas: list[float] = []
        for t in texts:
            for m in re.finditer(r"(\d+[\.,]?\d*)\s*(?:kv\.?\s*m|m²|m2)", t):
                try:
                    all_areas.append(float(m.group(1).replace(",", ".")))
                except ValueError:
                    pass
            if not plot:
                m = re.search(r"(\d+[\.,]?\d*)\s*a\b", t)
                if m:
                    plot = m.group(1)
            if not year:
                m = re.search(r"(\d{4})\s*m\.", t)
                if m:
                    year = m.group(1)
            if not rooms:
                m = re.search(r"(\d+)\s*kamb", t)
                if m:
                    rooms = m.group(1)
        if all_areas:
            main_area = max(a for a in all_areas if a < 1000)
            area = str(main_area)
        return area, plot, rooms, year

    def _extract_info_table(self, response: HtmlResponse) -> dict:
        info = {}
        for sel in [
            "table tr",
            "dl dt",
            "[class*='detail'] div",
            "[class*='info'] div",
            "[class*='param'] div",
        ]:
            rows = response.css(sel)
            if not rows:
                continue
            for row in rows:
                texts = row.css("::text").getall()
                texts = [t.strip() for t in texts if t.strip()]
                if len(texts) >= 2:
                    key = texts[0].rstrip(":").lower()
                    value = texts[-1]
                    if key and value and key != value.lower():
                        info[key] = value
            if info:
                break

        body_text = response.text
        for pattern, key in [
            (r"Namo tipas[:\s]+([^\n<]+)", "namo tipas"),
            (r"Šildymas[:\s]+([^\n<]+)", "šildymas"),
            (r"Aukštai[:\s]+(\d+)", "aukštai"),
            (r"Energijos klasė[:\s]+([A-G]\+*)", "energijos klasė"),
        ]:
            m = re.search(pattern, body_text, re.IGNORECASE)
            if m and key not in info:
                info[key] = m.group(1).strip()

        return info

    def _extract_address(self, title: str, response: HtmlResponse) -> str:
        if title:
            parts = re.split(r"\b(?:namas|butas|kotedžas|sodyba|namo dalis)\b", title, flags=re.I)
            if len(parts) > 1:
                addr = parts[-1].strip().strip(",").strip()
                if addr:
                    return addr

        breadcrumbs = response.css(
            "nav a::text, [class*='breadcrumb'] a::text, "
            "[class*='path'] a::text"
        ).getall()
        breadcrumbs = [b.strip() for b in breadcrumbs if b.strip() and b.strip() != "Pradžia"]
        if breadcrumbs:
            return ", ".join(breadcrumbs[-3:])

        if title:
            return title

        return ""

    def _extract_city(self, title: str, address: str) -> str:
        text = f"{title} {address}".lower()
        cities = [
            "Vilnius", "Kaunas", "Klaipėda", "Šiauliai", "Panevėžys",
            "Kretinga", "Palanga", "Alytus", "Marijampolė", "Mažeikiai",
            "Jonava", "Utena", "Kėdainiai", "Telšiai", "Tauragė",
            "Ukmergė", "Visaginas", "Plungė", "Raseiniai", "Biržai",
        ]
        for city in cities:
            if city.lower() in text:
                return city
        sav_match = re.search(r"(\w+)\s*(?:rajono|r\.)\s*sav", text)
        if sav_match:
            return sav_match.group(1).capitalize()
        return ""

    def _extract_description(
        self, response: HtmlResponse, all_text: list[str]
    ) -> str:
        for sel in [
            "[class*='description']::text",
            "[class*='comment']::text",
            "[class*='aprasymas']::text",
            "p::text",
        ]:
            texts = response.css(sel).getall()
            desc = " ".join(t.strip() for t in texts if len(t.strip()) > 30)
            if desc:
                return desc[:2000]

        for t in all_text:
            if len(t) > 50 and not re.match(r"^\d", t):
                return t[:2000]
        return ""

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
