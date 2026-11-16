"""Capital.lt spider: plain HTTP, server-rendered pages.

The houses page for the district (`.../nekilnojamas-turtas/pirkti/kretingos-r-sav/namai`) lists
every result on one page: `a.realty-item` cards, each linking to the house's own page ("rezultatai
(12)" in the page header, no pager). `?page=2` returns an empty list, which ends the walk through
`base.ListingSpider` ("no new links"), so a district that outgrew one page would need a different
paging scheme; the walk would stop quietly at page 1 rather than loop.

A house page has a facts table (`tr.realty-info-line`, plus a header/cell row for area, floors,
rooms, heating), the description, and a `gallery = [...]` JSON with the photos. It also carries a
broker block (name, phone, email) and the description may end with a phone number; this spider
never reads the broker block, and the scrub pipeline removes phones from text.

Terms: capital.lt declares `Content-Signal: ai-train=no, use=reference` in robots.txt. The text
here is input to judging (Jev), never training or fine-tuning data.

Start URL and args: the `capital` entry in `apps/listings/sources.py`.

Usage:
    scrapy crawl capital
    scrapy crawl capital -a max_pages=1
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime

from scrapy.http import Response
from scrapy.settings.default_settings import DOWNLOAD_HANDLERS_BASE

from realestate_spiders.items import ListingItem
from realestate_spiders.spiders.listings.base import IndexLink, ListingSpider
from realestate_spiders.spiders.listings.contact_lines import without_contact_lines

# Substring of the building-type text -> Listing building type.
BUILDING_TYPES = (
    ("mūr", "brick"),
    ("blok", "block"),
    ("medin", "wooden"),
    ("rąst", "log"),
    ("karkas", "frame"),
    ("monolit", "monolithic"),
)
SOURCE_ID = re.compile(r"-p(\d+)$")
GALLERY = re.compile(r"\bgallery\s*=\s*(\[.*?\])\s*;", re.DOTALL)
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
ENERGY_CLASS = re.compile(r"^[A-G]\+{0,2}$")
# A house sale page: ".../lt/parduodamas-namas-<slug>-p<id>".
HOUSE_PATH = "/lt/parduodamas-namas-"


def to_number(text: str | None) -> float | None:
    match = NUMBER.search((text or "").replace("\xa0", " "))
    return float(match.group().replace(",", ".")) if match else None


def to_int(text: str | None) -> int | None:
    value = to_number(text)
    return int(value) if value is not None else None


def digits(text: str | None) -> str | None:
    """Whole-euro price ("Kaina: 299,900 €" -> "299900"). Commas here are thousands
    separators, so they must not reach the normalizer, which reads a comma as a decimal point."""
    found = re.sub(r"\D", "", text or "")
    return found or None


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


class CapitalSpider(ListingSpider):
    name = "capital"
    allowed_domains = ["capital.lt"]

    custom_settings = {
        "DOWNLOAD_DELAY": 3,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        # Server-rendered pages: use Scrapy's own HTTP handler so no browser is started.
        "DOWNLOAD_HANDLERS": {
            "http": DOWNLOAD_HANDLERS_BASE["http"],
            "https": DOWNLOAD_HANDLERS_BASE["https"],
        },
    }

    def index_links(self, response: Response) -> Iterator[IndexLink]:
        for href in response.css("a.realty-item::attr(href)").getall():
            url = response.urljoin(href)
            # Counted as seen, not followed, when it is not a for-sale house page.
            yield IndexLink(url, HOUSE_PATH in url)

    def parse_detail(self, response: Response):
        title = clean(response.css("h1.realty-title::text").get())
        id_match = SOURCE_ID.search(response.url.split("?")[0])
        if not title or not id_match:
            self.logger.warning("Not a listing page: %s", response.url)
            return

        facts = {
            clean(row.css("td:nth-child(1)::text").get()): clean(
                " ".join(row.css("td:nth-child(2) ::text").getall())
            )
            for row in response.css("table.realty-main-info tr.realty-info-line")
        }
        if facts.get("Pardavimas ar nuoma", "Pardavimas").lower() != "pardavimas":
            self.logger.info("Not a sale ad, skipped: %s", response.url)
            return

        # The header row names the columns of the one summary row below it.
        headers = [
            clean(th) for th in response.css("table.realty-main-info thead th ::text").getall()
        ]
        cells = [
            clean(" ".join(td.css("::text").getall()))
            for td in response.css("table.realty-main-info tr.realty-main-info-top td")
        ]
        summary = dict(zip(headers, cells, strict=False))

        building_text = facts.get("Statinio tipas", "").lower()
        item = ListingItem(
            url=response.url.split("?")[0],
            source=self.name,
            source_id=id_match.group(1),
            title=title,
            description=self._description(response),
            price=digits(response.css(".realty-price-info strong::text").get()),
            property_type=self.target,
            listing_type="sale",
            area_sqm=to_number(summary.get("Plotas")),
            rooms=to_int(summary.get("Kambariai")),
            total_floors=to_int(summary.get("Aukštai")),
            year_built=facts.get("Statybos metai") or None,
            building_type=next((v for k, v in BUILDING_TYPES if k in building_text), ""),
            heating_type=summary.get("Šildymas", ""),
            address=facts.get("Adresas", ""),
            photo_urls=self._photos(response),
            raw_data={
                "facts": facts,
                "summary": summary,
                "price_per_sqm": clean(
                    " ".join(response.css(".realty-price-info-square ::text").getall())
                ),
            },
            scraped_at=datetime.now(UTC).isoformat(),
        )
        for label, value in facts.items():
            if "energ" in label.lower() and ENERGY_CLASS.match(value):
                item["energy_class"] = value
        yield item

    @staticmethod
    def _description(response: Response) -> str:
        # Direct text nodes only: the block also holds a "short link" anchor and view counters.
        lines = [clean(t) for t in response.css("div.realty-description::text").getall()]
        lines = [line for line in lines if line and not line.startswith("Trumpa nuoroda")]
        return "\n".join(without_contact_lines(lines))

    @staticmethod
    def _photos(response: Response) -> list[str]:
        match = GALLERY.search(response.text)
        if not match:
            return []
        try:
            gallery = json.loads(match.group(1))
        except ValueError:
            return []
        urls = [
            g.get("src") for g in gallery if isinstance(g, dict) and g.get("record_type") == "photo"
        ]
        return [u for u in urls if isinstance(u, str) and u.startswith("http")]
