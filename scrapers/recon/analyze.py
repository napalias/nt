"""Pure analysis for the site inspector: no browser, so it is unit-tested.

inspect_site.py collects raw page data with Playwright; this module turns it into
spider-building facts (card containers, pagination, endpoints) and a Markdown report.
"""

import re
from collections import Counter, defaultdict
from urllib.parse import parse_qsl, urlsplit

READ_ONLY_METHODS = {"GET", "HEAD", "OPTIONS"}

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
