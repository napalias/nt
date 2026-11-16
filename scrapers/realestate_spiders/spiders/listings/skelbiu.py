"""Skelbiu.lt spider: curl_cffi (Chrome TLS fingerprint), houses for sale in Kretinga district.

Recon (`scrapers/recon/SITES.md`, 2026-11-15 and a re-check on 2026-11-16): the search page
answers plain Scrapy, but listing pages answer it with a 403 wall. curl_cffi gets both, so
every request here goes through `realestate_spiders.handlers.CurlCffiDownloadHandler`: no
browser, no JavaScript.

    https://www.skelbiu.lt/paieska/namai-kretingoje/      page 1
    https://www.skelbiu.lt/paieska/namai-kretingoje/2     page 2, 3, ...

The start URL is a keyword search, not a category: it returns timber, roofing, flats, plots and
houses outside Kretinga alongside the houses. The index cards carry no category, so a listing
is judged on its own page and skipped (counted in `nt/details_skipped`) unless it
- sits under the "Namai" category breadcrumb,
- is in Kretinga district (the location text starts with the scan area's municipality), and
- is not priced per month.
Index links are only `a.standard-list-item` cards of the shape `/skelbimai/<slug>-<id>.html`;
there is no fallback that accepts other links.

Listing page: h1, `.price-container`, the `.details-row` label/value pairs, the description,
the location line, photos in JSON-LD. The seller card and contact buttons are never read, so
no contact data reaches an item; phone numbers typed into a description are removed by
`ScrubPipeline`.

The walk, registry args and stats are in `base.ListingSpider`. Start URL and args: the
`skelbiu` entry in `apps/listings/sources.py`.

Usage:
    scrapy crawl skelbiu
    scrapy crawl skelbiu -a max_pages=1
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from apps.listings.sources import SCAN_AREA
from scrapy.http import Response

from realestate_spiders.items import ListingItem
from realestate_spiders.lt_address import STREET_SUFFIXES
from realestate_spiders.spiders.listings.base import IndexLink, ListingSpider
from realestate_spiders.spiders.listings.fields import (
    BUILDING_TYPE_MAP,
    ENERGY_CLASS,
    ares,
    lt_point,
    number,
    whole_number,
    year,
)

# `/skelbimai/<slug>-87188154.html`: the one shape of a listing link.
LISTING_PATH = re.compile(r"^/skelbimai/[^/]+-(?P<id>\d+)\.html$")
HOUSE_CATEGORY = "/skelbimai/nekilnojamasis-turtas/namai/"
# Settlement kind abbreviations after the name: "Kumponų vs.", "Bruzdeilyno k.".
SETTLEMENT_KIND = re.compile(r"\s+(?:k|vs|m|mstl|gyv)\.$", re.IGNORECASE)
# Genitive -> nominative endings for place names, longest first ("Kumponų" -> "Kumponai").
GENITIVE_ENDINGS = (
    ("ių", "iai"),
    ("ų", "ai"),
    ("džio", "dis"),
    ("čio", "tis"),
    ("io", "is"),
    ("os", "a"),
    ("o", "as"),
)

DETAIL_LABELS = {
    "area": "namo plotas, m²",
    "rooms": "kamb. sk.",
    "floors": "aukštų skaičius",
    "year": "metai",
    "heating": "šildymas",
    "building_type": "tipas",
    "plot": "sklypo plotas, a",
    "energy": "energetinė klasė",
    "kind": "pastato tipas",
    "street": "gatvė",
    "settlement": "gyvenvietė",
}


def nominative(place: str) -> str:
    """ "Kumponų vs." -> "Kumponai". The portal prints villages in the genitive; the geocoder
    only knows nominative names. A best guess by ending; the printed text stays in raw_data."""
    name = SETTLEMENT_KIND.sub("", place.strip())
    for ending, replacement in GENITIVE_ENDINGS:
        if name.endswith(ending) and len(name) - len(ending) >= 3:
            return name[: -len(ending)] + replacement
    return name


class SkelbiuSpider(ListingSpider):
    name = "skelbiu"
    allowed_domains = ["skelbiu.lt"]

    custom_settings = {
        "DOWNLOAD_DELAY": 3,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_HANDLERS": {
            "http": "realestate_spiders.handlers.CurlCffiDownloadHandler",
            "https": "realestate_spiders.handlers.CurlCffiDownloadHandler",
        },
    }

    def page_url(self, start_url: str, page: int) -> str:
        """Page N is a trailing path segment: `/paieska/namai-kretingoje/N`; page 1 has none."""
        parts = urlsplit(start_url)
        base = re.sub(r"/\d+/?$", "", parts.path).rstrip("/")
        return urlunsplit(parts._replace(path=f"{base}/" if page == 1 else f"{base}/{page}"))

    def index_links(self, response: Response) -> Iterator[IndexLink]:
        for href in response.css("a.standard-list-item::attr(href)").getall():
            parts = urlsplit(response.urljoin(href))
            if LISTING_PATH.match(parts.path):
                yield IndexLink(urlunsplit(parts._replace(query="", fragment="")))

    def parse_detail(self, response: Response):
        source_id = LISTING_PATH.match(urlsplit(response.url).path)
        title = " ".join(response.css("h1 ::text").get("").split())
        if not source_id or not title:
            self.logger.warning("Not a listing page: %s", response.url)
            return

        crumbs = response.css("ol.breadcrumbs a::attr(href)").getall()
        if HOUSE_CATEGORY not in crumbs:
            self._skip(response, "not in the Namai category")
            return
        location = " ".join(response.css(".cities-name::text").get("").split())
        if not self._in_scan_area(location):
            self._skip(response, f"outside the scan area ({location or 'no location'})")
            return
        price_box = " ".join(response.css(".price-container ::text").getall())
        if "mėn" in price_box.lower():
            self._skip(response, "priced per month (rent)")
            return

        details = self._details(response)
        by_field = {field: details.get(label, "") for field, label in DETAIL_LABELS.items()}
        city = nominative(by_field["settlement"] or self._settlement(location))
        street = by_field["street"]
        address = ", ".join(part for part in (self._municipality(location), city, street) if part)

        item = ListingItem(
            url=response.url,
            source=self.name,
            source_id=source_id["id"],
            title=title,
            description=self._description(response),
            price=number(response.css(".price-container p.price ::text").get()),
            property_type=self.target,
            listing_type="sale",
            area_sqm=number(by_field["area"]),
            plot_area_ares=ares(by_field["plot"]),
            rooms=whole_number(by_field["rooms"]),
            year_built=year(by_field["year"]),
            total_floors=whole_number(by_field["floors"]),
            building_type=BUILDING_TYPE_MAP.get(by_field["building_type"].lower(), ""),
            heating_type=by_field["heating"],
            address=address,
            city=city,
            photo_urls=self._photos(response),
            raw_data={
                "details": details,
                "location": location,
                "price_per_sqm": " ".join(response.css(".price-for-unit::text").get("").split()),
            },
            scraped_at=datetime.now(UTC).isoformat(),
        )
        if ENERGY_CLASS.match(by_field["energy"]):
            item["energy_class"] = by_field["energy"]

        pin = response.css("[data-coordinates]")
        coords = (pin.attrib.get("data-coordinates") or "").split(",")
        point = lt_point(*coords) if len(coords) == 2 else None
        if point:
            item["latitude"], item["longitude"] = point
            approximate = pin.attrib.get("data-approximate") == "1"
            item["location_precision"] = (
                "city" if approximate else ("street" if street else "exact")
            )
        elif street.lower().endswith(STREET_SUFFIXES):
            item["location_precision"] = "street"
        yield item

    # --- helpers -------------------------------------------------------------------
    def _skip(self, response: Response, reason: str) -> None:
        self.logger.info("Skipping %s: %s", response.url, reason)
        self.crawler.stats.inc_value("nt/details_skipped")

    @staticmethod
    def _in_scan_area(location: str) -> bool:
        """ "Kretingos r., Kumponų vs." starts with the scan area's municipality "Kretingos r."."""
        prefix = SCAN_AREA.municipality.split(" sav")[0].lower()
        return location.lower().startswith(prefix)

    @staticmethod
    def _municipality(location: str) -> str:
        """First part of the location with the "sav." the geocoder looks for."""
        first = location.split(",")[0].strip()
        return f"{first} sav." if re.search(r"\b[rm]\.$", first) else first

    @staticmethod
    def _settlement(location: str) -> str:
        parts = [p.strip() for p in location.split(",")]
        return parts[1] if len(parts) > 1 else ""

    def _details(self, response: Response) -> dict[str, str]:
        """Label -> value from the `.details-row` pairs (label without its colon, lowercase)."""
        details: dict[str, str] = {}
        for row in response.css(".details-row"):
            label = " ".join(row.css("label::text").getall()).strip().rstrip(":").lower()
            value = " ".join(" ".join(row.css("span::text").getall()).split())
            if label and value:
                details[label] = value
        return details

    def _description(self, response: Response) -> str:
        nodes = response.xpath(
            '//div[contains(@class,"item-description")]//div[@class="description"]'
            "//text()[not(ancestor::a[contains(@class,'__cf_email__')])]"
        ).getall()
        text = "\n".join(" ".join(n.split()) for n in nodes if n.strip())
        return text.strip()

    def _photos(self, response: Response) -> list[str]:
        """Photo URLs from the listing's JSON-LD (linked, never re-hosted)."""
        for raw in response.css('script[type="application/ld+json"]::text').getall():
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("@type") == "RealEstateListing":
                return [u for u in data.get("image") or [] if isinstance(u, str)]
        return []
