"""Kretingos skelbimai (kretingosskelbimai.lt): a local classifieds board, plain HTML.

Its real-estate category mixes everything: house ads sit next to building materials, concrete
work, energy certificates and "wanted" ads. The registry therefore starts at the board's own
`Namai` (houses) and `Sodybos` (farmsteads) sub-categories, and this spider keeps only ads that
read as a house for sale (`is_house_for_sale`: a sale word, a house word, none of the rent,
wanted, service or materials words). Everything else is counted as seen and skipped. The same
check runs again on the full text at the detail page. A model screen can come later; this is the
cheap code filter.

The page has no structured address. The title (or the first sentence) usually names the place in
the locative ("... namas Guobu aklg., Kretingoje"), which the geocoder turns back into a
nominative; otherwise the poster's "Vietove" tag is used. The poster's name, phone and email sit
in `div.sk_contacts`, of which only the location tag is read, and the description's signature
block is dropped by `clean_lines`.

Pages: the board lists 25 ads a page with a `div#pager` ("/skelbimai/nekilnojamas-turtas/N.html").
A sub-category with one page has no pager and makes one request.

Usage:
    scrapy crawl kretingosskelbimai
    scrapy crawl kretingosskelbimai -a max_pages=1
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime

from scrapy.http import Response
from scrapy.settings.default_settings import DOWNLOAD_HANDLERS_BASE

from realestate_spiders.items import ListingItem
from realestate_spiders.lt_address import nominative_variants
from realestate_spiders.spiders.listings.base import IndexLink, ListingSpider
from realestate_spiders.spiders.listings.text import clean_lines, to_number

CAP = "A-ZĄČĘĖĮŠŲŪŽ"
LOW = "a-ząčęėįšųūž"

SALE = re.compile(r"parduod|pardavim|parduos", re.IGNORECASE)
HOUSE = re.compile(r"\b(?:nam(?:as|ą|ai|o|ų)|sodyb\w*|koted\w+)\b", re.IGNORECASE)
# Not a house for sale, judged on the title: rent, wanted, swaps, services, materials,
# certificates, plots for building.
NOT_FOR_SALE = re.compile(
    r"nuom|ieškom|ieškau|pirktum|perku\b|keičiu|darb\w*\b|paslaug|sertifikat|medžiag"
    r"|namo statyb|statybai|statybos darb|statybinių",
    re.IGNORECASE,
)
# A wanted ad can carry a sale word further down; these words in the opening mean wanted.
WANTED = re.compile(r"ieškom|ieškau|pirktum|\bperku\b", re.IGNORECASE)
STREET_PLACE = re.compile(
    rf"(?P<street>[{CAP}][\w{LOW}]+(?: [{CAP}][\w{LOW}]+)?\s+(?:g\.|aklg\.|pr\.|al\.|gatvėje))"
    rf",?\s+(?P<place>[{CAP}][{LOW}]+(?:iuose|uose|oje|ėje|yje|ose|yse))\b"
)
LOCATIVE = re.compile(rf"\b(?P<place>[{CAP}][{LOW}]+(?:iuose|uose|oje|ėje|yje|ose|yse))\b")
AREA = re.compile(r"(?:namo\s+|bendras\s+)?plotas\D{0,12}(\d+(?:[.,]\d+)?)\s*(?:kv|m2|m²)", re.I)
AREA_ANY = re.compile(r"(\d+(?:[.,]\d+)?)\s*kv\.?\s*m", re.IGNORECASE)
PLOT = re.compile(
    r"sklyp\w*[^\d\n]{0,40}(\d+(?:[.,]\d+)?)\s*a\b|(\d+(?:[.,]\d+)?)\s*a\.?\s+(?:\w+\s+){0,3}sklyp",
    re.IGNORECASE,
)
YEAR = re.compile(r"statybos\s+metai\D{0,6}((?:19|20)\d\d)", re.IGNORECASE)
ROOMS = re.compile(r"(\d+)\s*(?:-\s*)?(?:kambari|kamb\.)", re.IGNORECASE)


def is_house_for_sale(title: str, text: str = "") -> bool:
    """Whether an ad reads as a house for sale. `text` is the snippet or the description."""
    combined = f"{title} {text}"
    return bool(
        SALE.search(combined)
        and HOUSE.search(title)
        and not NOT_FOR_SALE.search(title)
        and not WANTED.search(text[:200])
    )


class KretingosskelbimaiSpider(ListingSpider):
    name = "kretingosskelbimai"
    allowed_domains = ["kretingosskelbimai.lt"]

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
        base = start_url.rstrip("/")
        return start_url if page <= 1 else f"{base}/{page}.html"

    def has_next_page(self, response: Response) -> bool:
        page = int(response.meta.get("page") or 1)
        return bool(response.css(f"div#pager a[href$='/{page + 1}.html']"))

    def index_links(self, response: Response) -> Iterator[IndexLink]:
        for card in response.css("div.sk-item"):
            href = card.css("a.t::attr(href)").get()
            if not href:
                continue
            title = " ".join(card.css("a.t::text").getall()).strip()
            snippet = " ".join(card.xpath("./text()").getall())
            yield IndexLink(response.urljoin(href), is_house_for_sale(title, snippet))

    # --- detail --------------------------------------------------------------------
    def parse_detail(self, response: Response):
        title = " ".join(response.css("h1.sk::text").getall()).strip()
        if not title:
            self.logger.warning("No listing on %s", response.url)
            return

        raw = response.css("div.sk_l div.info").get() or ""
        price_text = response.css("div.sk_l div.info span.price span::text").get()
        # the price span sits inside the description box: cut it before taking the text
        raw = re.sub(r'<span class="price">.*?</span></span>', "", raw, flags=re.DOTALL)
        lines = [re.sub(r"<[^>]+>", "", part) for part in re.split(r"<br\s*/?>", raw)]
        text = "\n".join(lines)
        if not is_house_for_sale(title, text):
            self.logger.info("Not a house for sale, skipped: %s", response.url)
            return

        tag = response.xpath(
            "//div[@class='sk_contacts']/span[normalize-space()='Vietovė:']"
            "/following-sibling::span[1]/text()"
        ).get("")
        street, place = self._place(title, text, tag.strip())
        description = clean_lines(lines)

        item = ListingItem(
            url=response.url,
            source=self.name,
            source_id=self._source_id(response.url),
            title=title,
            description=description,
            price=to_number(price_text) or None,
            property_type=self.target,
            listing_type="sale",
            area_sqm=self._first_number(text, AREA, AREA_ANY),
            plot_area_ares=self._first_number(text, PLOT),
            rooms=self._first_number(text, ROOMS),
            year_built=self._first_number(text, YEAR),
            address=", ".join(part for part in (street, place) if part),
            city=nominative_variants(place)[0] if place else "",
            photo_urls=self._photos(response),
            raw_data={
                "posted": (response.css("div.full_data::text").get() or "").strip(),
                "location_tag": tag.strip(),
            },
            scraped_at=datetime.now(UTC).isoformat(),
        )
        yield item

    @staticmethod
    def _place(title: str, text: str, tag: str) -> tuple[str, str]:
        """(street, place) for the geocoder. The title's locative place, else a `street, Place`
        pair in the opening of the text, else the poster's location tag."""
        match = STREET_PLACE.search(title)
        if match:
            return match["street"], match["place"]
        match = LOCATIVE.search(title)
        if match:
            return "", match["place"]
        match = STREET_PLACE.search(text[:300])
        if match:
            return match["street"], match["place"]
        return "", tag

    @staticmethod
    def _first_number(text: str, *patterns: re.Pattern[str]) -> float | None:
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return to_number(next(g for g in match.groups() if g))
        return None

    @staticmethod
    def _photos(response: Response) -> list[str]:
        """Large versions (`foto/l-...`) of the gallery's thumbnails (`foto/m-...`)."""
        urls = []
        for src in response.css("ul#sfm img::attr(src)").getall():
            url = response.urljoin(src.replace("/m-", "/l-", 1))
            if "no-img" not in url and url not in urls:
                urls.append(url)
        return urls

    @staticmethod
    def _source_id(url: str) -> str:
        match = re.search(r"/skelbimas/(\d+)-", url)
        return match.group(1) if match else ""
