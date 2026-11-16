"""Shared base for listing spiders that walk paged index pages and then each listing's page.

A subclass sets `name` (a source in `apps/listings/sources.py`) and fills two hooks:

    index_links(response)  -> list[IndexLink]   the listing links on one index page
    parse_detail(response) -> ListingItem(s)    one listing page into items

Everything else lives here:

- Config comes from the registry. Start URLs, `max_pages`, `region` and any other spider
  args are the registry entry's, for the target named by `-a target=house` (the one target
  when a source has only one). Explicit `-a` args win over the registry, so
  `scrapy crawl domoplius -a max_pages=1` works by hand and `run_spider` sends the same set.
- `async def start()` requests page 1 of every start URL. Each start URL is its own walk.
- A walk stops when a page yields no new links (past the last page, an empty page, a
  redirect back to page 1) or at `max_pages` (0 means no cap). It never trusts a page count.
- Stats: `nt/links_seen` counts distinct listing links found on index pages (including those
  not followed) and `nt/links_skipped` those a subclass chose not to follow.

Pages are `?page=N` by default; override `page_url` for any other scheme, and `has_next_page`
when the site shows whether another page exists. The first page is
always the start URL as registered (minus a `page` parameter), because some sites redirect
`?page=1` to the bare URL.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import scrapy
from apps.classifier import ingest
from apps.listings.sources import get_source
from scrapy.http import Request, Response


class IndexLink(NamedTuple):
    """A listing link found on an index page. `follow=False` counts it as seen but does not
    request it (a rental on a for-sale scan, say), so a page of only such links does not end
    the walk."""

    url: str
    follow: bool = True


class ListingSpider(scrapy.Spider):
    page_param = "page"

    def __init__(self, target: str | None = None, **kwargs) -> None:
        source = get_source(self.name)
        chosen = source.target(target or None)
        args = {**source.crawl_args(chosen, {}), **kwargs}
        super().__init__(**args)
        self.target: str = chosen.property_type
        self.start_urls = list(chosen.start_urls)
        self.max_pages = int(getattr(self, "max_pages", 0) or 0)
        self._seen_links: set[str] = set()

    # --- hooks ---------------------------------------------------------------------
    def index_links(self, response: Response) -> Iterable[IndexLink]:
        """Every listing link on one index page."""
        raise NotImplementedError

    def parse_detail(self, response: Response):
        """Turn one listing page into items."""
        raise NotImplementedError

    def defer_house_check(self, response: Response, reason: str) -> bool:
        """Where a mixed-feed spider's code filter says "not a house for sale": whether to
        build the item anyway and let the judge decide.

        JEV_MODE=off: no. The page is counted in `nt/details_skipped` and skipped, as always.
        shadow and on: yes. The caller yields the item with `_house_ad_heuristic=False`;
        JevScreenPipeline asks Jev, logs both answers, and drops the item unless Jev (on)
        says it is a house for sale after all. The page is already fetched, so this costs no
        request. Hard filters (the scan area, say) stay skips: only house-ness is judged.
        """
        if ingest.screens_active():
            self.logger.info("Kept for the judge %s: %s", response.url, reason)
            return True
        self.logger.info("Skipping %s: %s", response.url, reason)
        self.crawler.stats.inc_value("nt/details_skipped")
        return False

    def has_next_page(self, response: Response) -> bool:
        """Whether index page `response` can have a successor. The default is yes (the walk then
        ends on a page with no new links); a site that shows its page links, or lists everything on
        one page, overrides this so no request is wasted on a page that does not exist."""
        return True

    def page_url(self, start_url: str, page: int) -> str:
        """URL of index page `page` of the walk that starts at `start_url`."""
        parts = urlsplit(start_url)
        query = [(k, v) for k, v in parse_qsl(parts.query) if k != self.page_param]
        if page > 1:
            query.append((self.page_param, str(page)))
        return urlunsplit(parts._replace(query=urlencode(query)))

    # --- the walk ------------------------------------------------------------------
    async def start(self):  # Scrapy >= 2.13 entry point; start_requests() is no longer called
        for url in self.start_urls:
            yield self._index_request(url, 1)

    def _index_request(self, start_url: str, page: int) -> Request:
        return Request(
            self.page_url(start_url, page),
            callback=self.parse_index,
            meta={"index_url": start_url, "page": page},
        )

    def parse_index(self, response: Response):
        start_url, page = response.meta["index_url"], response.meta["page"]
        links = list({link.url: link for link in self.index_links(response)}.values())
        new = [link for link in links if link.url not in self._seen_links]
        self._seen_links.update(link.url for link in new)

        stats = self.crawler.stats
        stats.inc_value("nt/links_seen", len(new))
        stats.inc_value("nt/links_skipped", sum(1 for link in new if not link.follow))
        self.logger.info("Page %d of %s: %d new links of %d", page, start_url, len(new), len(links))

        if not new:
            self.logger.info("No new links on page %d of %s, stopping", page, start_url)
            return
        for link in new:
            if link.follow:
                yield Request(link.url, callback=self.parse_detail)

        if self.max_pages and page >= self.max_pages:
            self.logger.info("Reached max_pages=%d for %s, stopping", self.max_pages, start_url)
            return
        if not self.has_next_page(response):
            self.logger.info("No next page after page %d of %s, stopping", page, start_url)
            return
        yield self._index_request(start_url, page + 1)
