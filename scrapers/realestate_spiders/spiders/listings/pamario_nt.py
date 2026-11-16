"""Pamario NT (pamario-nt.lt): a Klaipeda agency with a Kretinga houses page. Plain HTML.

robots.txt asks for `Crawl-delay: 10`; the registry entry sets that delay and the spider's own
default matches it.

The Kretinga houses start URL (`/lt/kretinga/namai---`) is one object: when the category holds
a single listing the site redirects straight to its page (`/lt/nta/<id>-namas-kretinga`), so
the "index" response is already the detail page and `parse_index` reads it as one. With more
objects the page is a list of `a.paieska_nt_inline` cards (shape taken from the agency's
Klaipeda houses page, which has five); the list shows "Rasti: N objektai" and no page links
were seen, so a next page is requested only when N is more than the cards on the page.

The detail page holds the broker's name, email and phone in a contact box. This spider does not
read it, and `clean_lines` drops the contact lines in the description.

Usage:
    scrapy crawl pamario-nt
    scrapy crawl pamario-nt -a max_pages=1
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import urlsplit

from scrapy.http import Response
from scrapy.settings.default_settings import DOWNLOAD_HANDLERS_BASE

from realestate_spiders.items import ListingItem
from realestate_spiders.spiders.listings.base import IndexLink, ListingSpider
from realestate_spiders.spiders.listings.text import clean_lines, to_number

BUILDING_TYPE_MAP = {
    "mūrinis": "brick",
    "blokinis": "block",
    "medinis": "wooden",
    "monolitinis": "monolithic",
    "rąstinis": "log",
    "karkasinis": "frame",
}
# The agency's closing paragraph, the same on every listing.
BOILERPLATE = re.compile(
    r"^_{5,}|pamario|skambu|skambin|objekto apžiūra|bendradarbiaujame|palankios paskolų"
    r"|juridiškai teisingai|daugiau pasiūlymų",
    re.IGNORECASE,
)


class PamarioNtSpider(ListingSpider):
    name = "pamario-nt"
    allowed_domains = ["pamario-nt.lt"]

    custom_settings = {
        "DOWNLOAD_DELAY": 10,  # robots.txt Crawl-delay
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_HANDLERS": {
            "http": DOWNLOAD_HANDLERS_BASE["http"],
            "https": DOWNLOAD_HANDLERS_BASE["https"],
        },
    }

    # --- index ---------------------------------------------------------------------
    def parse_index(self, response: Response):
        if self._is_detail(response):
            # A category with one object redirects to it: this response is the listing.
            self.crawler.stats.inc_value("nt/links_seen")
            self.logger.info("Start URL redirected to one listing: %s", response.url)
            yield from self.parse_detail(response)
            return
        yield from super().parse_index(response)

    def index_links(self, response: Response) -> Iterator[IndexLink]:
        for href in response.css("a.paieska_nt_inline::attr(href)").getall():
            if "/lt/nta/" in href:
                yield IndexLink(response.urljoin(href))

    def has_next_page(self, response: Response) -> bool:
        total = to_number(response.css("span.paieska_filter_blue::text").get())
        cards = len(response.css("a.paieska_nt_inline"))
        more = bool(total and total > cards)
        if more:
            self.logger.warning(
                "Page lists %d of %d objects; the paging scheme is unverified", cards, int(total)
            )
        return more

    @staticmethod
    def _is_detail(response: Response) -> bool:
        return urlsplit(response.url).path.startswith("/lt/nta/")

    # --- detail --------------------------------------------------------------------
    def parse_detail(self, response: Response):
        kind = " ".join(response.css("span.obj_title::text").getall()).strip()
        if not kind:
            self.logger.warning("No listing on %s", response.url)
            return
        if "pardavimui" not in kind.lower():  # "Namas nuomai" and the like
            self.logger.info("Not a sale ad (%r): %s", kind, response.url)
            return

        facts = self._facts(response)
        place = facts.get("Adresas", "").strip(" ,")
        municipality = facts.get("Savivaldybė", "").strip()
        raw_description = response.css("div.obj-description").get() or ""
        lines = [
            re.sub(r"<[^>]+>", "", part)
            for part in re.split(r"<br\s*/?>", raw_description)
            if not BOILERPLATE.search(part)
        ]

        item = ListingItem(
            url=response.url,
            source=self.name,
            source_id=(response.css("span.obj_name_other_color::text").get() or "").strip(),
            title=f"{kind}, {place}" if place else kind,
            description=clean_lines(lines),
            price=to_number(response.css("span.obj_price b::text").get()) or None,
            property_type=self.target,
            listing_type="sale",
            area_sqm=to_number(facts.get("Plotas")),
            plot_area_ares=to_number(facts.get("Sklypo plotas")),
            rooms=to_number(facts.get("Kambarių skaičius")),
            year_built=to_number(facts.get("Pastatymo metai")),
            building_type=BUILDING_TYPE_MAP.get(facts.get("Namo tipas", "").strip().lower(), ""),
            heating_type=facts.get("Šildymas", ""),
            address=", ".join(part for part in (place, municipality) if part),
            city=place.split(",")[0].strip(),
            municipality=municipality,
            photo_urls=[
                response.urljoin(src)
                for src in response.css("#product_slider img::attr(src)").getall()
            ],
            raw_data={"facts": facts, "condition": facts.get("Būklė", "")},
            scraped_at=datetime.now(UTC).isoformat(),
        )
        floors = to_number(facts.get("Aukštų"))
        if floors:
            item["total_floors"] = int(floors)
        if not item["source_id"]:
            match = re.search(r"/nta/(\d+)", response.url)
            item["source_id"] = match.group(1) if match else ""
        yield item

    @staticmethod
    def _facts(response: Response) -> dict[str, str]:
        """The object table: label and value cells alternate in one flat list of divs, after
        the two "Objektas" header cells."""
        cells = [
            " ".join(cell.css("::text").getall()).strip()
            for cell in response.css("div.obj-table-block > div")
        ]
        cells = cells[2:] if cells[:2] == ["Objektas", "Objektas"] else cells
        facts = {}
        for label, value in zip(cells[0::2], cells[1::2], strict=False):
            label = label.rstrip(":").strip()
            if label and value:
                facts[label] = value
        return facts
