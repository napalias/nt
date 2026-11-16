"""Domoplius.lt spider: plain HTTP, reading the JSON the site embeds in every page.

domoplius is an Inertia app. The server HTML carries the page's data as JSON in
`div#app[data-page]`, so no browser and no CSS scraping:

- index page: `props.properties` (20 cards: url, saleType, price, ...), `props.pagination`.
- listing page: `props.property` with the description, price, overview (area, plot, rooms,
  year), location (city, street, coordinates), details and images. It also carries a
  `contact` block; this spider never reads it, so contact data cannot reach an item.

Index pages are `<start url>?page=N` (page 1 is the bare URL). The walk, registry args and
stats are in `base.ListingSpider`. Start URL and args: the `domoplius` entry in
`apps/listings/sources.py`.

Usage:
    scrapy crawl domoplius
    scrapy crawl domoplius -a max_pages=1
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime

from scrapy.http import Response
from scrapy.settings.default_settings import DOWNLOAD_HANDLERS_BASE

from realestate_spiders.items import ListingItem
from realestate_spiders.lt_address import STREET_SUFFIXES
from realestate_spiders.spiders.listings.base import IndexLink, ListingSpider

BUILDING_TYPE_MAP = {
    "mūrinis": "brick",
    "blokinis": "block",
    "medinis": "wooden",
    "monolitinis": "monolithic",
    "rąstinis": "log",
    "karkasinis": "frame",
}

# "Gyvenamojo namo dalis Kretingos r. sav., Kretingoje, Trakų g." -> kind + place. The place
# starts at the municipality and is what the geocoder understands.
TITLE_PLACE = re.compile(
    r"^(?P<kind>.+?)\s+(?P<place>\S+\s+(?:r\.|raj\.|rajono|m\.|miesto)\s*sav\..*)$"
)
ENERGY_CLASS = re.compile(r"^[A-G]\+{0,2}$")
# Rough box around Lithuania; a coordinate outside it is a bad value, not a location.
LT_LAT, LT_LON = (53.8, 56.6), (20.9, 26.9)


def embedded_json(response: Response) -> dict:
    """The page's `div#app[data-page]` props, or {} when absent or unreadable."""
    raw = response.css("div#app::attr(data-page)").get()
    if not raw:
        return {}
    try:
        return json.loads(raw).get("props") or {}
    except (ValueError, AttributeError):
        return {}


def lt_point(coordinates: object) -> tuple[float, float] | None:
    """(lat, lon) when the portal gave a usable point, else None."""
    if not isinstance(coordinates, dict):
        return None
    try:
        lat, lon = float(coordinates.get("lat")), float(coordinates.get("lon"))
    except (TypeError, ValueError):
        return None
    if LT_LAT[0] <= lat <= LT_LAT[1] and LT_LON[0] <= lon <= LT_LON[1]:
        return lat, lon
    return None


class DomopliusSpider(ListingSpider):
    name = "domoplius"
    allowed_domains = ["domoplius.lt"]

    custom_settings = {
        "DOWNLOAD_DELAY": 3,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        # The project default routes requests through the browser-rendering download handler.
        # This site needs none of it: use Scrapy's own HTTP handler so no browser is started.
        "DOWNLOAD_HANDLERS": {
            "http": DOWNLOAD_HANDLERS_BASE["http"],
            "https": DOWNLOAD_HANDLERS_BASE["https"],
        },
    }

    def index_links(self, response: Response) -> Iterator[IndexLink]:
        # The index mixes sales and rentals ("pardavimas ir nuoma"), and `?action_type=1` is
        # dropped by a redirect, so keep for-sale ads only, here. Counted as seen, not followed.
        for card in embedded_json(response).get("properties") or []:
            if card.get("url"):
                yield IndexLink(response.urljoin(card["url"]), card.get("saleType") == "sale")

    def parse_detail(self, response: Response):
        prop = embedded_json(response).get("property")
        if not prop:
            self.logger.warning("No embedded listing data on %s", response.url)
            return

        overview = prop.get("overview") or {}
        details = prop.get("details") or {}
        location = prop.get("location") or {}
        price = prop.get("price") or {}
        stats = prop.get("stats") or {}

        address = self._address(prop.get("title") or "", location)
        item = ListingItem(
            url=response.url,
            source=self.name,
            source_id=str(prop.get("id") or ""),
            title=prop.get("title") or "",
            description=(prop.get("description") or "").strip(),
            price=price.get("total"),
            property_type=self.target,
            listing_type="sale",
            area_sqm=overview.get("size"),
            plot_area_ares=overview.get("landSize"),
            rooms=overview.get("rooms"),
            year_built=overview.get("yearBuilt"),
            total_floors=details.get("floors"),
            building_type=BUILDING_TYPE_MAP.get(
                str(details.get("constructionType") or "").lower(), ""
            ),
            heating_type=details.get("heatingType") or "",
            address=address,
            city=location.get("city") or "",
            photo_urls=[u.split("?")[0] for u in prop.get("images") or []],
            raw_data={
                "overview": overview,
                "details": details,
                "price_per_sqm": price.get("perSqM"),
                "listed_at": stats.get("addTime"),
                "edited_at": stats.get("editTime"),
            },
            scraped_at=datetime.now(UTC).isoformat(),
        )
        if ENERGY_CLASS.match(str(details.get("energyCertificate") or "")):
            item["energy_class"] = details["energyCertificate"]

        point = lt_point(location.get("coordinates"))
        if point:
            item["latitude"], item["longitude"] = point
            # With no street the pin is the town (about 450 m from Kretinga's centre on the
            # saved page), so say so rather than let the geocoder pipeline call it exact.
            has_street = bool(location.get("street")) or any(
                part.strip().lower().endswith(STREET_SUFFIXES) for part in address.split(",")
            )
            item["location_precision"] = "street" if has_street else "city"

        if not item["source_id"]:
            match = re.search(r"-(\d+)\.html", response.url)
            item["source_id"] = match.group(1) if match else ""
        yield item

    def _address(self, title: str, location: dict) -> str:
        """Address for the geocoder: the title's place part, plus the street if it is only in
        the location block. Falls back to the location block when the title has no municipality."""
        match = TITLE_PLACE.match(title)
        if match:
            address = match["place"]
        else:
            address = ", ".join(
                part for part in (location.get("city"), location.get("microdistrict")) if part
            )
        street = location.get("street")
        if street and street.lower() not in address.lower():
            address = f"{address}, {street}" if address else street
        return address
