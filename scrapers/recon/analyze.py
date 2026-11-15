"""Pure analysis for the site inspector: no browser, so it is unit-tested.

inspect_site.py collects raw page data with Playwright; this module turns it into
spider-building facts (card containers, pagination, endpoints) and a Markdown report.
"""

import re
from collections import Counter, defaultdict
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}

# Politeness (principal's rules): 3 s between requests to one host, 10 s for hosts whose
# robots.txt asks for a Crawl-delay; hosts we must never touch at all.
DEFAULT_DELAY_S = 3
SLOW_HOSTS = {"pamario-nt.lt": 10, "ntseptynetas.lt": 10}
FORBIDDEN_HOSTS = {"system.rasknt.lt"}  # RaskNT back-office: never requested

# A fetch "saw the listing page" when it holds at least this many distinct links, and at
# least half of what the browser saw (a JS shell has a few links, a listing page dozens).
MIN_ANCHORS = 10
MIN_ANCHOR_RATIO = 0.5
MIN_COVERAGE = 0.9  # share of the browser's same-site links a plain fetch must already hold
WALL_RANK = {"none": 0, "cookie_wall": 1, "login": 2, "captcha": 3}

CHALLENGE_TITLES = ("just a moment", "attention required", "access denied", "are you human",
                    "verify you are human", "checking your browser", "pardon our interruption")
CHALLENGE_MARKERS = ("cf-chl", "px-captcha", "perimeterx", "captcha-delivery", "datadome",
                     "cf-browser-verification", "challenge-platform", "press &amp; hold")
CONSENT_MARKERS = ("cybotcookiebot", "onetrust", "cookieconsent", "cookie-consent", "cc-window",
                   "didomi", "slapukų sutikimas", "slapuku sutikimas", "consent.google")
LOGIN_URL = re.compile(
    r"/(login|log-in|signin|sign-in|prisijungti|prisijungimas|prisijunk)\b", re.IGNORECASE
)
PASSWORD_INPUT = re.compile(r"""<input[^>]+type\s*=\s*["']?password""", re.IGNORECASE)


def is_forbidden_host(host: str) -> bool:
    return host.lower().split(":")[0] in FORBIDDEN_HOSTS


def host_delay_s(host: str, robots_delay: float | None = None) -> float:
    """Seconds to wait between two requests to one host: never below 3, 10 for the slow ones."""
    bare = host.lower().split(":")[0].removeprefix("www.")
    return max(DEFAULT_DELAY_S, SLOW_HOSTS.get(bare, 0), robots_delay or 0)


def robots_allows(robots_txt: str, url: str, agent: str = "*") -> bool:
    """Whether robots.txt lets `agent` fetch `url`. Empty robots allows everything."""
    parser = RobotFileParser()
    parser.parse(robots_txt.splitlines())
    return parser.can_fetch(agent, url)


def robots_crawl_delay(robots_txt: str, agent: str = "*") -> float | None:
    parser = RobotFileParser()
    parser.parse(robots_txt.splitlines())
    delay = parser.crawl_delay(agent)
    return float(delay) if delay is not None else None


class _PageScan(HTMLParser):
    """Title and the distinct real hrefs of a page: all the verdict needs from the HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.hrefs: set[str] = set()
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "title":
            self._in_title = True
        elif tag == "a":
            href = (dict(attrs).get("href") or "").strip()
            if href and not href.startswith(("#", "javascript:", "mailto:", "tel:")):
                self.hrefs.add(href)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


def _scan(html: str) -> _PageScan:
    scan = _PageScan()
    scan.feed(html)
    scan.close()
    scan.title = re.sub(r"\s+", " ", scan.title).strip()
    return scan


def detect_wall(status: int | None, final_url: str, title: str, html: str) -> str:
    """What stands between a fetch and the listing: captcha, login, cookie_wall, or none.

    A recaptcha widget, cookie banner or login box on a page full of listings is not a wall;
    walls are judged on the status, the title and how thin the page is.
    """
    head = html[:30_000].lower()
    anchors = len(_scan(html).hrefs) if html else 0
    thin = anchors < MIN_ANCHORS
    if any(t in title.lower() for t in CHALLENGE_TITLES):
        return "captcha"
    if any(m in head for m in CHALLENGE_MARKERS) and (thin or (status or 0) >= 400):
        return "captcha"
    if (status or 0) in (403, 429) and "captcha" in head:
        return "captcha"
    if status == 401 or LOGIN_URL.search(urlsplit(final_url).path):
        return "login"
    if thin and PASSWORD_INPUT.search(html[:200_000]):
        return "login"
    if thin and any(m in head for m in CONSENT_MARKERS):
        return "cookie_wall"
    return "none"


def summarize_html(
    method: str, status: int | None, final_url: str, html: str, error: str = ""
) -> dict:
    """One fetch reduced to what the verdict compares: status, wall, distinct link count."""
    if error or not html:
        return {"method": method, "status": status, "final_url": final_url, "title": "",
                "wall": "none" if error else detect_wall(status, final_url, "", html),
                "anchors": 0, "hrefs": set(), "bytes": len(html), "error": error}
    scan = _scan(html)
    hrefs = {urldefrag(urljoin(final_url, h))[0] for h in scan.hrefs}
    return {"method": method, "status": status, "final_url": final_url, "title": scan.title,
            "wall": detect_wall(status, final_url, scan.title, html), "anchors": len(scan.hrefs),
            "hrefs": hrefs, "bytes": len(html), "error": ""}


def request_allowed(method: str, url: str, allow_post: tuple[str, ...] = ()) -> bool:
    """The browser's read-only guard: GET/HEAD/OPTIONS, plus POSTs the caller named.

    Some portals load their listing with a POST that only searches (realu's getEstates);
    `allow_post` lets those URL fragments through and nothing else.
    """
    if is_read_only(method):
        return True
    return method.upper() == "POST" and any(part in url for part in allow_post)


def _host_key(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def href_coverage(fetch: dict, browser: dict) -> float | None:
    """Share of the browser's same-site links that also appear in this fetch's raw HTML.

    A server-rendered listing page has them all; a JS-rendered one lacks the cards. None when
    the browser saw too few same-site links to say.
    """
    if not fetch.get("hrefs") and not browser.get("hrefs"):
        return None
    host = _host_key(browser.get("final_url", ""))
    reference = {h for h in browser.get("hrefs", ()) if _host_key(h) == host}
    if len(reference) < MIN_ANCHORS:
        return None
    return len(reference & fetch.get("hrefs", set())) / len(reference)


def _usable(fetch: dict | None, reference_anchors: int, coverage: float | None) -> bool:
    if not fetch or fetch.get("error") or fetch.get("status") != 200 or fetch.get("wall") != "none":
        return False
    if coverage is not None:
        return coverage >= MIN_COVERAGE
    return fetch["anchors"] >= max(MIN_ANCHORS, MIN_ANCHOR_RATIO * reference_anchors)


def decide_verdict(
    plain: dict | None, curl: dict | None, browser: dict | None
) -> tuple[str, str, str]:
    """(verdict, wall, reason): the cheapest fetch that still sees the listing page.

    plain < curl_cffi < playwright; `blocked` when none of them gets listing content.
    "Sees the listing page" means the fetch holds at least 90% of the same-site links the
    browser ended up with (so a JS shell wrapped in a 150-link menu does not pass); without
    link sets it falls back to comparing link counts. Adds `coverage` to each result.
    """
    browser_ok = bool(browser) and browser.get("status") == 200 and browser.get("wall") == "none"
    reference = browser["anchors"] if browser_ok else 0
    for fetch in (plain, curl):
        if fetch and browser_ok:
            fetch["coverage"] = href_coverage(fetch, browser)
    attempts = [("plain", plain), ("curl_cffi", curl), ("playwright", browser)]
    for name, fetch in attempts:
        coverage = fetch.get("coverage") if fetch and name != "playwright" else None
        if _usable(fetch, reference if name != "playwright" else 0, coverage):
            if name == "plain":
                return "plain", "none", "plain HTTP returned the listing page"
            earlier = ", ".join(
                f"{n} {_describe(f)}" for n, f in attempts[: [a[0] for a in attempts].index(name)]
            )
            note = f"{earlier}; " if earlier else ""
            if name == "playwright":
                note += "needs JS rendering: the server HTML has no listing links"
            else:
                note += "TLS/browser impersonation gets through"
            return name, "none", note
    walls = [f["wall"] for _, f in attempts if f and not f.get("error")]
    wall = max(walls, key=WALL_RANK.get, default="none")
    detail = "; ".join(f"{n} {_describe(f)}" for n, f in attempts if f)
    return "blocked", wall, f"no fetch returned listing content ({detail})"


def _describe(fetch: dict | None) -> str:
    if not fetch:
        return "not run"
    if fetch.get("error"):
        return f"error: {fetch['error'][:60]}"
    coverage = fetch.get("coverage")
    seen = f", {coverage:.0%} of browser links" if coverage is not None else ""
    return f"HTTP {fetch.get('status')}, {fetch.get('anchors')} links{seen}, wall {fetch.get('wall')}"

PAGINATION_TEXT = re.compile(
    r"^(\d{1,4}|[›»→>]+|.*\b(kitas|toliau|sekantis|next|kitą)\b.*)$", re.IGNORECASE
)


PAGINATION_HREF = re.compile(r"[?&](page|page_nr|pg|p|puslapis)=\d+|/(puslapis|page)/\d+", re.IGNORECASE)


def _site_domain(url: str) -> str:
    """Registered domain of a URL (last two host labels), e.g. www.domoplius.lt → domoplius.lt."""
    return ".".join(urlsplit(url).netloc.split(".")[-2:])


def is_read_only(method: str) -> bool:
    """The inspector only lets these through, so clicking around can never submit anything."""
    return method.upper() in READ_ONLY_METHODS


def safe_url(url: str) -> str:
    """URL with query values removed (keys kept): enough to see the API shape, no data."""
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}{parts.path}"
    keys = [k for k, _ in parse_qsl(parts.query, keep_blank_values=True)]
    return f"{base}?{'&'.join(keys)}" if keys else base


def card_patterns(links: list[dict], min_count: int = 4, top: int = 5) -> list[dict]:
    """Containers that wrap many links: the likely listing cards a spider should iterate."""
    by_container: dict[str, list[dict]] = defaultdict(list)
    for item in links:
        if item.get("container"):
            by_container[item["container"]].append(item)
    ranked = sorted(by_container.items(), key=lambda kv: len(kv[1]), reverse=True)
    return [
        {
            "container": container,
            "count": len(items),
            "sample_hrefs": [i["href"] for i in items[:3]],
            "sample_texts": [i.get("text", "")[:60] for i in items[:3]],
        }
        for container, items in ranked
        if len(items) >= min_count
    ][:top]


def pagination_links(links: list[dict]) -> list[dict]:
    """Links that look like page numbers or next-page controls."""
    return [
        item
        for item in links
        if item.get("rel") == "next"
        or PAGINATION_TEXT.match((item.get("text") or "").strip())
        or PAGINATION_HREF.search(item.get("href") or "")
    ]


def _cell(value: object, limit: int = 60) -> str:
    text = str(value or "").replace("|", "\\|").replace("\n", " ").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _element_rows(elements: list[dict], kind: str, limit: int) -> list[str]:
    # The same control on every card ("Save" ×20) is one row with a count.
    groups: dict[tuple[str, str], list[dict]] = {}
    for e in elements:
        if e["kind"] == kind:
            groups.setdefault((e.get("text", ""), e.get("selector", "")), []).append(e)
    if not groups:
        return ["_none_", ""]
    lines = [
        "| Text | id | classes | name | selector (matches) | × |",
        "|---|---|---|---|---|---|",
    ]
    for (_, _), group in list(groups.items())[:limit]:
        e = group[0]
        lines.append(
            f"| {_cell(e.get('text'))} | {_cell(e.get('id'), 30)} "
            f"| {_cell(' '.join(e.get('classes') or []), 50)} | {_cell(e.get('name'), 25)} "
            f"| `{_cell(e.get('selector'), 70)}` ({e.get('matches', '?')}) | ×{len(group)} |"
        )
    if len(groups) > limit:
        lines.append(f"\n_{len(groups) - limit} more distinct in pages.json_")
    return lines + [""]


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _render_fetch(fetch: dict) -> list[str]:
    out = [
        "### Fetch verdict",
        "",
        f"- Verdict: `{fetch['verdict']}`",
        f"- Wall: `{fetch['wall']}`",
        f"- Why: {fetch['reason']}",
        "",
    ]
    if fetch.get("robots_disallowed"):
        out += ["robots.txt disallows this URL, so nothing was fetched (that is the finding).", ""]
    elif fetch.get("results"):
        out += ["| Method | HTTP | Wall | Distinct links | Share of browser links | Bytes "
                "| Final URL | Error |",
                "|---|---|---|---|---|---|---|---|"]
        out += [
            f"| {r['method']} | {r.get('status')} | {r['wall']} | {r['anchors']} "
            f"| {_percent(r.get('coverage'))} | {r.get('bytes', 0)} "
            f"| {_cell(safe_url(r.get('final_url', '')), 70)} | {_cell(r.get('error'), 50)} |"
            for r in fetch["results"]
        ]
        out.append("")
    return out


def render_report(site: str, pages: list[dict]) -> str:
    """Human/agent-readable summary of everything the inspector found."""
    out = [f"# Site inspection: {site}", ""]
    out.append("Read-only Playwright run: every non-GET request was blocked. Full data: `pages.json`.")
    out.append("")
    for n, page in enumerate(pages, 1):
        elements = page.get("elements", [])
        links = [e for e in elements if e["kind"] == "link"]
        out += [
            f"## Page {n}: {_cell(page.get('title'), 80)}",
            "",
            f"- URL: {page.get('url')}",
            f"- HTTP status: {page.get('status')}",
            f"- Screenshot: `{page.get('screenshot')}`",
            f"- Elements: {sum(1 for e in elements if e['kind'] == 'button')} buttons, "
            f"{len(links)} links, {sum(1 for e in elements if e['kind'] == 'input')} inputs",
            "",
        ]
        if page.get("fetch"):
            out += _render_fetch(page["fetch"])
        if page.get("challenge"):
            out += [f"**Bot challenge detected:** {page['challenge']}", ""]

        out += ["### Buttons and clickables", ""] + _element_rows(elements, "button", 60)
        out += ["### Inputs", ""] + _element_rows(elements, "input", 40)

        cards = card_patterns(links)
        out += ["### Repeated link containers (listing-card candidates)", ""]
        if cards:
            out += ["| Container | Links | Sample hrefs |", "|---|---|---|"]
            out += [
                f"| `{_cell(c['container'], 70)}` | {c['count']} "
                f"| {_cell(', '.join(c['sample_hrefs']), 120)} |"
                for c in cards
            ]
        else:
            out.append("_none_")
        out.append("")

        pager = pagination_links(links)
        out += ["### Pagination candidates", ""]
        out += [f"- `{_cell(p.get('text'), 20)}` → {_cell(p.get('href'), 100)}" for p in pager[:15]]
        out += ["_none_"] if not pager else []
        out.append("")

        forms = page.get("forms", [])
        out += ["### Forms", ""]
        for f in forms:
            fields = ", ".join(
                f"{fld.get('name') or fld.get('id') or '?'}:{fld.get('type')}" for fld in f["fields"]
            )
            out.append(f"- `{f.get('selector')}` {f.get('method', 'get').upper()} "
                       f"{_cell(f.get('action'), 80)} — {_cell(fields, 200)}")
        out += ["_none_"] if not forms else []
        out.append("")

        network = page.get("network", [])
        out += ["### XHR / fetch / JSON endpoints", ""]
        if network:
            counts = Counter((r["method"], r["status"], r["url"]) for r in network)
            keys = {r["url"]: r.get("json_keys", "") for r in network}
            site = _site_domain(page.get("url", ""))
            first_party = lambda u: _site_domain(u) == site  # noqa: E731
            # The site's own API first: that's what a spider might call instead of parsing HTML.
            ordered = sorted(counts.items(), key=lambda kv: (not first_party(kv[0][2]), -kv[1]))
            out += [
                "| Party | Method | Status | URL (query keys only) | JSON keys | × |",
                "|---|---|---|---|---|---|",
            ]
            out += [
                f"| {'site' if first_party(u) else 'third-party'} | {m} | {s} | {_cell(u, 110)} "
                f"| {_cell(keys.get(u), 60)} | {c} |"
                for (m, s, u), c in ordered[:30]
            ]
        else:
            out.append("_none_")
        out.append("")

        blocked = page.get("blocked", [])
        if blocked:
            out += ["### Blocked writes (read-only guard)", ""]
            out += [f"- {b}" for b in sorted(set(blocked))[:20]]
            out.append("")
    return "\n".join(out)
