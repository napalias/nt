"""Realu.lt spider: plain HTTP, no browser. The list comes from the site's own search endpoint.

The listing page is server-rendered chrome with an empty results area; a script fills it by
POSTing the search form to `index.php?route=product/category/getEstates`, which answers JSON
(`total, per_page, current_page, last_page, view`, where `view` is the cards' HTML). This spider
makes the same two calls without a browser:

1. GET the registered start URL (`.../nekilnojamasis-turtas/pirkti/namas/kretingos-r-sav`) and
   read the search form on it (operation, estate type, municipality ids and slugs). The form
   is the source of truth for the area, so nothing about Kretinga is hard-coded here.
2. POST those fields with `page=N` to the endpoint; one card per house, linking to its page.

Then each house page is parsed for the description, plot area, year, heating and photos.
The page also carries a broker block (name, phone); this spider never reads it, so contact
data cannot reach an item.

Pages are numbered by the form field `page`, not by the URL. The walk, registry args and stats
are in `base.ListingSpider`. Start URL and args: the `realu` entry in `apps/listings/sources.py`.

robots.txt (checked 2026-11-16) has no rule for the endpoint or the listing pages.

Usage:
    scrapy crawl realu
    scrapy crawl realu -a max_pages=1
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import urljoin

from scrapy import Selector
from scrapy.http import FormRequest, Request, Response
from scrapy.settings.default_settings import DOWNLOAD_HANDLERS_BASE

from realestate_spiders.items import ListingItem
from realestate_spiders.spiders.listings.base import IndexLink, ListingSpider
from realestate_spiders.spiders.listings.contact_lines import without_contact_lines

ENDPOINT_PATH = "/index.php?route=product/category/getEstates"

# Form fields the page's own script sends (defaults + slugs); we read them from the page.
FORM_FIELDS = (
    "op",
    "estate_type",
    "country_id",
    "abroad_city_id",
    "mls_municipality_id",
    "mls_city_id",
    "op_slug",
    "estate_type_slug",
    "municipality_slug",
    "country_slug",
    "abroad_city_slug",
)

BUILDING_TYPE_MAP = {
    "mūrinis": "brick",
    "blokinis": "block",
    "medinis": "wooden",
    "rąstinis": "log",
    "karkasinis": "frame",
    "monolitinis": "monolithic",
}
# Card type labels of things that are not houses (the search is already filtered to houses;
# this is the guard for a registry URL that drifts).
NOT_HOUSE = ("butas", "sklyp", "patalp", "garaž", "vieta")
# "Parduodamas namas Jokūbavo k., Kretingos r. sav., 66 m2 ploto, 2 aukštai": address is the
# part after the kind and before the size. Sizes use `m2`, `m²` or `m` and a decimal comma or point.
TITLE_ADDRESS = re.compile(
    r"^Parduodam\w*\s+\w+\s+(?P<address>.+?)\s*,\s*\d[\d.,\s]*\s*m\S{0,2}\s+ploto\b", re.IGNORECASE
)
TITLE_PREFIX = re.compile(r"^Parduodam\w*\s+\w+\s+", re.IGNORECASE)
ESTATE_ID = re.compile(r"realu(\d+)/?$")
ENERGY_CLASS = re.compile(r"^[A-G]\+{0,2}$")
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def to_number(text: str | None) -> float | None:
    """First number in `text` ("66.00 m", "160,39") as a float, else None."""
    match = NUMBER.search((text or "").replace("\xa0", " "))
    return float(match.group().replace(",", ".")) if match else None


def to_int(text: str | None) -> int | None:
    value = to_number(text)
    return int(value) if value is not None else None


def digits(text: str | None) -> str | None:
    """Only the digits of a whole-euro price ("75 000 €" -> "75000"), else None."""
    found = re.sub(r"\D", "", text or "")
    return found or None


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def search_form(response: Response) -> dict[str, str]:
    """The fields of the page's search form that the results endpoint takes."""
    form: dict[str, str] = {}
    for name in FORM_FIELDS:
        selected = response.css(f'#filter-form select[name="{name}"] option[selected]::attr(value)')
        value = selected.get()
        if value is None:
            value = response.css(f'#filter-form input[name="{name}"]::attr(value)').get()
        if value is not None:
            form[name] = value
    return form


class RealuSpider(ListingSpider):
    name = "realu"
    allowed_domains = ["realu.lt"]

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

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._forms: dict[str, dict[str, str]] = {}  # start URL -> search form fields

    # --- the walk: GET the search page once, then POST the results endpoint per page ---------
    async def start(self):
        for url in self.start_urls:
            yield Request(url, callback=self.parse_search_page, meta={"index_url": url})

    def parse_search_page(self, response: Response):
        start_url = response.meta["index_url"]
        form = search_form(response)
        if form.get("op") != "sale" or form.get("estate_type") != "house":
            self.logger.error(
                "%s is not a houses-for-sale search (op=%r, estate_type=%r); not crawling it",
                start_url,
                form.get("op"),
                form.get("estate_type"),
            )
            return
        # The page's own defaults for the two list controls, so the results match the page.
        self._forms[start_url] = {**form, "sort": "default", "limit": "32"}
        yield self._index_request(start_url, 1)

    def _index_request(self, start_url: str, page: int) -> Request:
        return FormRequest(
            urljoin(start_url, ENDPOINT_PATH),
            formdata={**self._forms[start_url], "page": str(page)},
            callback=self.parse_index,
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": start_url},
            meta={"index_url": start_url, "page": page},
        )

    def index_links(self, response: Response) -> Iterator[IndexLink]:
        try:
            view = json.loads(response.text).get("view") or ""
        except (ValueError, AttributeError):
            self.logger.warning("Results endpoint did not return JSON: %s", response.url)
            return
        for card in Selector(text=view).css("div.card-product[data-estate-id]"):
            href = card.css("a.info::attr(href)").get()
            if not href:
                continue
            kind = clean(" ".join(card.css("a.info .type::text").getall())).lower()
            for_sale_house = href.startswith("/parduodam") and not any(
                word in kind for word in NOT_HOUSE
            )
            yield IndexLink(response.urljoin(href), for_sale_house)

    # --- one house page -------------------------------------------------------------------
    def parse_detail(self, response: Response):
        title = clean(response.css('meta[property="og:title"]::attr(content)').get()) or clean(
            " ".join(response.css("h1.title ::text").getall())
        )
        id_match = ESTATE_ID.search(response.url.split("?")[0])
        if not title or not id_match:
            self.logger.warning("Not a listing page: %s", response.url)
            return

        meta = {
            clean(row.css(".dt::text").get()): clean(" ".join(row.css(".dd ::text").getall()))
            for row in response.css(".bottom .meta .dl")
        }
        info = {
            clean(row.css("strong::text").get()): clean(" ".join(row.css("span ::text").getall()))
            for row in response.css("#accordion-1 .accordion-body > div")
        }
        if info.get("Pardavimas ar nuoma", "Pardavimas").lower() != "pardavimas":
            self.logger.info("Not a sale ad, skipped: %s", response.url)
            return

        plot = self._plot_ares(meta.get("Sklypo plotas"))
        item = ListingItem(
            url=response.url.split("?")[0],
            source=self.name,
            source_id=id_match.group(1),
            title=title,
            description=self._description(response),
            price=digits(response.css("h1.title .price::text").get()),
            property_type=self.target,
            listing_type="sale",
            area_sqm=to_number(meta.get("Plotas")),
            plot_area_ares=plot,
            rooms=to_int(meta.get("Kambariai")),
            year_built=info.get("Statybos metai") or None,
            total_floors=to_int(meta.get("Aukštai")),
            building_type=BUILDING_TYPE_MAP.get(info.get("Namo tipas", "").lower(), ""),
            heating_type=info.get("Šildymas", ""),
            address=self._address(title, response),
            photo_urls=self._photos(response, id_match.group(1)),
            raw_data={
                "meta": meta,
                "info": info,
                "price_per_sqm": clean(response.css("h1.title .area::text").get()),
            },
            scraped_at=datetime.now(UTC).isoformat(),
        )
        for label, value in info.items():
            if "energ" in label.lower() and ENERGY_CLASS.match(value):
                item["energy_class"] = value
        yield item

    @staticmethod
    def _plot_ares(text: str | None) -> float | None:
        """Plot size in ares from "15.00 a", "0.15 ha" or "1500 m2"; None when unreadable."""
        value = to_number(text)
        if value is None:
            return None
        unit = clean(text).lower().split(" ", 1)[-1] if " " in clean(text) else "a"
        if unit.startswith("ha"):
            return value * 100
        if unit.startswith("m"):
            return value / 100
        return value

    @staticmethod
    def _description(response: Response) -> str:
        # The text is hard-wrapped into one <p> per source line, so join them with spaces.
        parts = [clean(p) for p in response.css("#accordion-3 .height-limit p ::text").getall()]
        return clean(" ".join(without_contact_lines(parts)))

    @staticmethod
    def _address(title: str, response: Response) -> str:
        match = TITLE_ADDRESS.match(title)
        if match:
            return match["address"]
        if TITLE_PREFIX.match(title):
            return TITLE_PREFIX.sub("", title)
        return clean(" ".join(response.css("h1.title .address ::text").getall()))

    @staticmethod
    def _photos(response: Response, estate_id: str) -> list[str]:
        """Full-width photo URLs of this listing, in page order, without the page's other
        estates (the 'similar' carousels)."""
        pattern = re.compile(rf"https://[^\"' ]+/estate_{estate_id}/[^\"' ]+?-cw\.(?:jpg|webp)")
        seen: dict[str, None] = {}
        for url in pattern.findall(response.text):
            seen.setdefault(url, None)
        return list(seen)
