"""Alio.lt spider: curl_cffi (Chrome TLS fingerprint), houses for sale in Kretinga district.

Recon (`scrapers/recon/SITES.md`, 2026-11-15) said plain, curl_cffi and a browser all see the
same pages. That was measured with `requests`; Scrapy's own HTTP client gets a 403 from the same
URL (checked 2026-11-16), while curl_cffi gets the page. So every request here goes through
`realestate_spiders.handlers.CurlCffiDownloadHandler`: no browser, no JavaScript. The start URL
is the search page for the district with category 1433 (houses, farmsteads, garden houses for
sale):

    https://www.alio.lt/paieska/kretingos-r-sav/?category_id=1433      page 1
    https://www.alio.lt/paieska/kretingos-r-sav/2/?category_id=1433    page 2, 3, ...

- index page: 24 cards, `a.main_a_c_b`, each linking to `/skelbimai/<slug>/ID<digits>.html`.
  Only links of that shape count; there is no fallback that accepts other links.
- listing page: h1, `og:price`, the `.av2-fact` label/value rows, the description in
  `#av2-desc`, the place in `.av2-title__place`, the map pin in `[data-av2-vmap]`, and the
  photos and breadcrumbs in JSON-LD. The seller block (`.av2-contact`) is never read, so no
  contact data reaches an item; phone numbers the seller typed into the description are
  removed by `ScrubPipeline`.

The walk, registry args and stats are in `base.ListingSpider`. Start URL and args: the `alio`
entry in `apps/listings/sources.py`.

Usage:
    scrapy crawl alio
    scrapy crawl alio -a max_pages=1
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

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

# `/skelbimai/<slug>/ID69851715.html`: the one shape of a listing link.
LISTING_PATH = re.compile(r"^/skelbimai/[^/]+/ID(?P<id>\d+)\.html$")
# The category every listing must sit under (breadcrumb URL of "Namai, sodybos, sodai").
HOUSE_CATEGORY = "/nekilnojamas-turtas/namai-sodybos-kotedzai/"
# What the map's `data-precision` says about the pin. Unknown values count as the town.
PRECISION = {"city": "city", "street": "street", "exact": "exact", "house": "exact"}

FACT_LABELS = {
    "area": "plotas",
    "rooms": "kambarių skaičius",
    "floors": "aukštų skaičius",
    "year": "statybos metai",
    "heating": "šildymas",
    "building_type": "namo tipas",
    "plot": "sklypo plotas",
    "energy": "energinė klasė",
    "kind": "pastato tipas",
}


class AlioSpider(ListingSpider):
    name = "alio"
    allowed_domains = ["alio.lt"]

    custom_settings = {
        "DOWNLOAD_DELAY": 3,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "DOWNLOAD_HANDLERS": {
            "http": "realestate_spiders.handlers.CurlCffiDownloadHandler",
            "https": "realestate_spiders.handlers.CurlCffiDownloadHandler",
        },
    }

    def page_url(self, start_url: str, page: int) -> str:
        """Page N is a path segment before the query: `/paieska/<region>/N/?category_id=...`."""
        parts = urlsplit(start_url)
        path = re.sub(r"/\d+/?$", "/", parts.path)
        if not path.endswith("/"):
            path += "/"
        if page > 1:
            path += f"{page}/"
        return urlunsplit(parts._replace(path=path))

    def index_links(self, response: Response) -> Iterator[IndexLink]:
        for href in response.css("a.main_a_c_b::attr(href)").getall():
            parts = urlsplit(response.urljoin(href))
            if LISTING_PATH.match(parts.path):
                yield IndexLink(urlunsplit(parts._replace(query="", fragment="")))

    def parse_detail(self, response: Response):
        source_id = LISTING_PATH.match(urlsplit(response.url).path)
        title = " ".join(response.css("h1.av2-h1::text").get("").split())
        if not source_id or not title:
            self.logger.warning("Not a listing page: %s", response.url)
            return
        if not self._is_house_for_sale(response):
            self._skip(response, "not a house for sale")
            return

        facts = self._facts(response)
        by_field = {field: facts.get(label, "") for field, label in FACT_LABELS.items()}
        place = " ".join(response.css(".av2-title__place > span::text").get("").split())
        city, has_street = self._city(place)

        item = ListingItem(
            url=response.url,
            source=self.name,
            source_id=source_id["id"],
            title=title,
            description=self._description(response),
            price=response.css('meta[name="og:price"]::attr(content)').get()
            or number(response.css(".av2-price__main::text").get()),
            property_type=self.target,
            listing_type="sale",
            area_sqm=number(by_field["area"]),
            plot_area_ares=ares(by_field["plot"]),
            rooms=whole_number(by_field["rooms"]),
            year_built=year(by_field["year"]),
            total_floors=whole_number(by_field["floors"]),
            building_type=BUILDING_TYPE_MAP.get(by_field["building_type"].lower(), ""),
            heating_type=by_field["heating"],
            address=place,
            city=city,
            photo_urls=self._photos(response),
            raw_data={
                "facts": facts,
                "listed_at": response.css("time::attr(datetime)").get(),
                "price_per_sqm": " ".join(response.css(".av2-price__sub::text").get("").split()),
            },
            scraped_at=datetime.now(UTC).isoformat(),
        )
        if ENERGY_CLASS.match(by_field["energy"]):
            item["energy_class"] = by_field["energy"]

        pin = response.css("[data-av2-vmap]")
        point = lt_point(pin.attrib.get("data-lat"), pin.attrib.get("data-lng"))
        if point:
            item["latitude"], item["longitude"] = point
            # An unlabelled pin is the town centre, not the house; never call it exact.
            item["location_precision"] = PRECISION.get(pin.attrib.get("data-precision", ""), "city")
        elif has_street:
            item["location_precision"] = "street"
        yield item

    # --- helpers -------------------------------------------------------------------
    def _skip(self, response: Response, reason: str) -> None:
        self.logger.info("Skipping %s: %s", response.url, reason)
        self.crawler.stats.inc_value("nt/details_skipped")

    def _is_house_for_sale(self, response: Response) -> bool:
        """Breadcrumbs (JSON-LD) say what the ad is. Without them the ad is not trusted."""
        for raw in response.css('script[type="application/ld+json"]::text').getall():
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("@type") == "BreadcrumbList":
                crumbs = data.get("itemListElement") or []
                names = {str(c.get("name") or "").lower() for c in crumbs}
                urls = [str(c.get("item") or "") for c in crumbs]
                return "parduoda" in names and any(HOUSE_CATEGORY in u for u in urls)
        return False

    def _facts(self, response: Response) -> dict[str, str]:
        """Label -> value from every `.av2-fact` row (chips such as "Elektra" are joined)."""
        facts: dict[str, str] = {}
        for row in response.css(".av2-fact"):
            label = " ".join(row.css("dt ::text").getall()).strip().lower()
            parts = row.css("dd ::text").getall()
            value = ", ".join(" ".join(p.split()) for p in parts if p.strip())
            if label and value:
                facts[label] = value
        return facts

    def _description(self, response: Response) -> str:
        lines = response.css("#av2-desc .av2-desc__line")
        text = "\n".join(
            " ".join(part.split()) for line in lines for part in line.xpath(".//text()").getall()
        )
        return re.sub(r"\n{2,}", "\n", text).strip()

    def _photos(self, response: Response) -> list[str]:
        """Photo URLs from the listing's JSON-LD (linked, never re-hosted)."""
        for raw in response.css('script[type="application/ld+json"]::text').getall():
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("@type") == "Product":
                return [u for u in data.get("image") or [] if isinstance(u, str)]
        return []

    def _city(self, place: str) -> tuple[str, bool]:
        """(settlement, has_street) from "Kretingos r. sav., Kretinga, Šventosios g."."""
        parts = [p.strip() for p in place.split(",") if p.strip()]
        has_street = any(p.lower().endswith(STREET_SUFFIXES) for p in parts)
        settlements = [
            p for p in parts if "sav." not in p.lower() and not p.lower().endswith(STREET_SUFFIXES)
        ]
        return (settlements[0] if settlements else ""), has_street
