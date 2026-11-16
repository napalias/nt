"""Meguvos bustas (meguvosbustas.lt): a Kretinga agency's own site, plain HTML, no JS.

`/paieska` lists every object the agency has (houses `/parduodami-namai/...`, flats
`/parduodami-butai/...`, plots `/sklypai/...`, premises), 9 cards a page, in the Kretinga and
Klaipeda districts. Pages are `/paieska/puslapis-N`. This spider follows only the house cards
whose region segment is the configured region (`kretinga`, so "Kretinga" and "Kretingos r.",
not "Klaipedos r."). Every other card is counted as seen and skipped.

Gotchas:
- The card hrefs carry raw spaces ("/parduodami-namai/Kretingos r./5-kambariu-/649/"). The site's
  firewall answers 403 to the usual `%20` and 200 to `+`, so spaces are sent as `+`.
- Every page carries the agent's name, phone and email (the contact box, the description's
  footer, the page footer). This spider reads none of the contact box, and `clean_lines`
  drops the description lines that point to the agent.

Usage:
    scrapy crawl meguvosbustas
    scrapy crawl meguvosbustas -a max_pages=1
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import quote, unquote, urljoin

from scrapy.http import Response
from scrapy.settings.default_settings import DOWNLOAD_HANDLERS_BASE

from realestate_spiders.items import ListingItem
from realestate_spiders.spiders.listings.base import IndexLink, ListingSpider
from realestate_spiders.spiders.listings.text import clean_lines, to_number, with_sav

HOUSE_PREFIX = "/parduodami-namai/"
BUILDING_TYPE_MAP = {
    "mūrinis": "brick",
    "blokinis": "block",
    "medinis": "wooden",
    "monolitinis": "monolithic",
    "rąstinis": "log",
    "karkasinis": "frame",
}
# The agency's standing ad text, the same on every listing: not about the property.
BOILERPLATE = re.compile(
    r"svarbu|dėmesio|pirkėjams|m[eė]guvos|nekilnojamojo turto pirkimo|tarpininkavimas"
    r"|finansinis konsultavimas",
    re.IGNORECASE,
)
# "Šiaulių g. 50", "Vynuogių g. 12", "Žibalės g.": a street, not a settlement.
STREET_PART = re.compile(r"\b(?:g|al|pr|pl|skg|kel|tak|aklg)\.(?:\s*\d+\w*)?$")
TITLE_SUFFIX = re.compile(r"\s*-\s*Meguvosbustas\.lt\s*$", re.IGNORECASE)


def site_url(base: str, href: str) -> str:
    """Absolute URL for a card href, spaces as `+` (a `%20` gets a 403 from the site)."""
    path = quote(href.strip(), safe="/,+%")
    return urljoin(base, path.replace("%20", "+"))


class MeguvosbustasSpider(ListingSpider):
    name = "meguvosbustas"
    allowed_domains = ["meguvosbustas.lt"]

    custom_settings = {
        "DOWNLOAD_DELAY": 3,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_HANDLERS": {
            "http": DOWNLOAD_HANDLERS_BASE["http"],
            "https": DOWNLOAD_HANDLERS_BASE["https"],
        },
    }

    # --- index ---------------------------------------------------------------------
    def page_url(self, start_url: str, page: int) -> str:
        return start_url if page <= 1 else f"{start_url.rstrip('/')}/puslapis-{page}"

    def has_next_page(self, response: Response) -> bool:
        page = int(response.meta.get("page") or 1)
        return bool(response.css(f"ul.pagination a[href$='puslapis-{page + 1}']"))

    def index_links(self, response: Response) -> Iterator[IndexLink]:
        stem = self.region[:7].lower()  # "kreting": the town and the district, not Kretingale
        for href in response.css("div.listing-wrapper > a::attr(href)").getall():
            if not href.strip():
                continue
            is_house = href.startswith(HOUSE_PREFIX)
            region_segment = unquote(href).strip("/").split("/")[1:2]
            in_area = bool(region_segment) and region_segment[0].lower().startswith(stem)
            yield IndexLink(site_url(response.url, href), is_house and in_area)

    # --- detail --------------------------------------------------------------------
    def parse_detail(self, response: Response):
        facts = self._facts(response)
        city = " ".join(
            response.css("div.specsBorder div.location div.city::text").getall()
        ).strip()
        street = " ".join(
            response.css("div.specsBorder div.location div.street::text").getall()
        ).strip()
        price = to_number(response.css("div.specsBorder div.price::text").get())
        title = TITLE_SUFFIX.sub("", (response.css("title::text").get() or "").strip())
        if not title:
            self.logger.warning("No listing on %s", response.url)
            return

        # the description is <br>-separated text; split the HTML, not its text
        raw = response.css("div.descBorder div.txt").get() or ""
        lines = [
            re.sub(r"<[^>]+>", "", part)
            for part in re.split(r"<br\s*/?>", raw)
            if not BOILERPLATE.search(part)
        ]

        item = ListingItem(
            url=response.url,
            source=self.name,
            source_id=self._source_id(response.url),
            title=title,
            description=clean_lines(lines),
            price=price if price else None,
            property_type=self.target,
            listing_type="sale",
            area_sqm=to_number(facts.get("Pastato plotas")),
            plot_area_ares=to_number(facts.get("Sklypo plotas")),
            rooms=to_number(facts.get("Kambarių sk.")),
            year_built=to_number(facts.get("Statybos metai")),
            building_type=BUILDING_TYPE_MAP.get(facts.get("Namo tipas", "").strip().lower(), ""),
            heating_type=facts.get("Šildymas", ""),
            address=self._address(city, street),
            city=self._city(city, street),
            photo_urls=[
                urljoin(response.url, u)
                for u in response.css("a[data-fancybox]::attr(href)").getall()
            ],
            raw_data={"facts": facts, "region": city},
            scraped_at=datetime.now(UTC).isoformat(),
        )
        floors = to_number(facts.get("Aukštai"))
        if floors:
            item["total_floors"] = int(floors)
        yield item

    def _facts(self, response: Response) -> dict[str, str]:
        """The label/value rows of the specs table. Yes/no rows (a div with class yes/no) are
        skipped: they carry no text."""
        facts: dict[str, str] = {}
        for row in response.css("div.specsData tr"):
            label = " ".join(row.css("th::text").getall()).strip().rstrip(":")
            value = " ".join(row.css("td::text").getall()).strip()
            if label and value:
                facts[label] = value
        return facts

    @staticmethod
    def _city(region: str, street: str) -> str:
        """The settlement: the first part of the street line that is not a street ("Rubulių km.",
        "Vydmantai"), else the town the site names, else nothing (a bare district)."""
        for part in (p.strip() for p in street.split(",")):
            if part and not STREET_PART.search(part):
                return re.sub(r"\s+(?:km|k)\.$", "", part)
        return "" if region.endswith(" r.") else region

    @staticmethod
    def _address(city: str, street: str) -> str:
        """`street, city` for the geocoder; a district ("Kretingos r.") gets its "sav."."""
        place = with_sav(city)
        return ", ".join(part for part in (street, place) if part)

    @staticmethod
    def _source_id(url: str) -> str:
        match = re.search(r"/(\d+)/?$", url)
        return match.group(1) if match else ""
