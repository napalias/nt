"""Alginos NT (alginosnt.lt): a Kretinga-district agency, a Laravel site, plain HTML.

The nationwide houses list is 15 pages, so the start URL is the site's own filter for the
district: `/search?itemType=namas&region=25` (25 is "Kretingos r." in the municipality select).
A text search (`search=Kretinga`) also matches Kretingale, which is in Klaipeda district.
Pages are `&page=N`; the pager links are read, so a one-page result makes one request.

Cards (`div.item.desktop2`, each with a `mobile2` twin) are not links: the target sits in an
`onclick="location='.../nekilnojamas-turtas/skelbimas/<id>'"`. The listing page has a label/value
table, the description and the agent's box (name, phone, email), which is not read here.
The page `<title>` says "butas" for houses, so the title is built from the table instead.

Usage:
    scrapy crawl alginosnt
    scrapy crawl alginosnt -a max_pages=1
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime

from scrapy.http import Response
from scrapy.settings.default_settings import DOWNLOAD_HANDLERS_BASE

from realestate_spiders.items import ListingItem
from realestate_spiders.spiders.listings.base import IndexLink, ListingSpider
from realestate_spiders.spiders.listings.text import clean_lines, to_number, with_sav

BUILDING_TYPE_MAP = {
    "mūrinis": "brick",
    "blokinis": "block",
    "medinis": "wooden",
    "monolitinis": "monolithic",
    "rąstinis": "log",
    "karkasinis": "frame",
}
CARD_TARGET = re.compile(r"location='(https?://[^']+/nekilnojamas-turtas/skelbimas/\d+)'")
IMAGES = re.compile(r"const\s+images\s*=\s*\[(.*?)\]", re.DOTALL)
PAGE_PARAM = re.compile(r"[?&]page=(\d+)")


class AlginosntSpider(ListingSpider):
    name = "alginosnt"
    allowed_domains = ["alginosnt.lt"]

    custom_settings = {
        "DOWNLOAD_DELAY": 3,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_HANDLERS": {
            "http": DOWNLOAD_HANDLERS_BASE["http"],
            "https": DOWNLOAD_HANDLERS_BASE["https"],
        },
    }

    # --- index ---------------------------------------------------------------------
    def index_links(self, response: Response) -> Iterator[IndexLink]:
        for card in response.css("div.item.desktop2"):
            onclick = " ".join(card.css("[onclick]::attr(onclick)").getall())
            match = CARD_TARGET.search(onclick)
            if match:
                yield IndexLink(match.group(1))

    def has_next_page(self, response: Response) -> bool:
        page = int(response.meta.get("page") or 1)
        pages = [
            int(m.group(1))
            for href in response.css("a::attr(href)").getall()
            if (m := PAGE_PARAM.search(href))
        ]
        return any(p > page for p in pages)

    # --- detail --------------------------------------------------------------------
    def parse_detail(self, response: Response):
        facts = self._facts(response)
        if not facts.get("Skelbimo ID"):
            self.logger.warning("No listing on %s", response.url)
            return

        street = facts.get("Gatvė", "")
        city = facts.get("Miestas", "")
        municipality = facts.get("Savivaldybė", "")
        kind = facts.get("Tipas", "")
        description = response.css("div.desc p").xpath("string(.)").get("")

        item = ListingItem(
            url=response.url,
            source=self.name,
            source_id=facts["Skelbimo ID"],
            title=", ".join(part for part in (kind, street, city) if part) or "Namas",
            description=clean_lines(description.split("\n")),
            price=to_number(facts.get("Kaina")) or None,
            property_type=self.target,
            listing_type="sale",
            area_sqm=to_number(facts.get("Plotas")),
            plot_area_ares=to_number(facts.get("Sklypo plotas")),
            rooms=to_number(facts.get("Kambarių sk.")),
            year_built=to_number(facts.get("Pastatymo metai")),
            building_type=BUILDING_TYPE_MAP.get(facts.get("Pastato tipas", "").lower(), ""),
            heating_type=facts.get("Šildymas", ""),
            address=", ".join(part for part in (street, city, with_sav(municipality)) if part),
            city=city,
            municipality=with_sav(municipality),
            photo_urls=self._photos(response),
            raw_data={"facts": facts},
            scraped_at=datetime.now(UTC).isoformat(),
        )
        # "Aukštas: 0/2" is floor/storeys of the building.
        floors = re.search(r"/\s*(\d+)", facts.get("Aukštas", ""))
        if floors:
            item["total_floors"] = int(floors.group(1))
        yield item

    @staticmethod
    def _facts(response: Response) -> dict[str, str]:
        facts = {}
        for row in response.css("div.right table tr"):
            cells = [" ".join(td.css("::text").getall()).strip() for td in row.css("td")]
            if len(cells) >= 2 and cells[0].rstrip(":") and cells[1]:
                facts[cells[0].rstrip(":").strip()] = cells[1]
        return facts

    @staticmethod
    def _photos(response: Response) -> list[str]:
        for script in response.css("script::text").getall():
            match = IMAGES.search(script)
            if match:
                return [response.urljoin(u) for u in re.findall(r"""["']([^"']+)["']""", match[1])]
        return []
