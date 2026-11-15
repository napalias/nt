"""Read-only site inspector: fetch a portal three ways, give a verdict, map what can be clicked.

    pnpm inspect domoplius              # a preset (see SITES)
    pnpm inspect https://... --follow 2 # any URL, then 2 listing-card links
    pnpm inspect rasknt --document-only # one request per fetch, no subresources

Each URL is fetched with plain HTTP (what Scrapy sends), then curl_cffi (Chrome TLS
impersonation), then headless Chromium. report.md opens with the verdict (`plain`,
`curl_cffi`, `playwright` or `blocked`) and the wall kind (`captcha`, `login`,
`cookie_wall`, `none`), then maps cards, pagination and XHR endpoints from the browser DOM.

Politeness: robots.txt is read first and a disallowed URL is not fetched (the report records
that as the finding); 3 s between requests to one host (10 s for hosts with a Crawl-delay);
system.rasknt.lt is never requested. No captcha solving, no stealth, no logins.

Runs inside the scraper_worker container (Playwright + Chromium + curl_cffi installed).
Writes scrapers/recon/out/<site>-<time>/: report.md (read this), pages.json, page-NN.png.

Read-only like the funnel-inspector spikes: every request that isn't GET/HEAD/OPTIONS
is aborted and listed, so nothing on the site can be submitted or changed. Response
bodies are never stored; only URL paths, query KEYS and top-level JSON keys.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from playwright.sync_api import Page, Response, Route, sync_playwright

from recon.analyze import (
    card_patterns,
    decide_verdict,
    host_delay_s,
    is_forbidden_host,
    render_report,
    request_allowed,
    robots_allows,
    robots_crawl_delay,
    safe_url,
    summarize_html,
)

# One preset per source in NT_SOURCES.md; each starts at the "houses for sale in Kretinga
# district" listing page (found by recon, see SITES.md). The two gov presets are for the
# permit/planning spiders.
SITES = {
    "aruodas": "https://www.aruodas.lt/namai/kretingoje/",
    "realu": "https://www.realu.lt/nekilnojamasis-turtas/pirkti/namas/kretingos-r-sav",
    "domoplius": "https://domoplius.lt/skelbimai/namai-kotedzai-sodai/kretinga",
    "alio": "https://www.alio.lt/paieska/kretingos-r-sav/?category_id=1433",
    "skelbiu": "https://www.skelbiu.lt/paieska/namai-kretingoje/",
    "rasknt": "https://rasknt.lt/namai/kretinga/",
    "capital": "https://www.capital.lt/lt/nekilnojamas-turtas/pirkti/kretingos-r-sav/namai",
    "meguvosbustas": "https://meguvosbustas.lt/paieska",
    "pamario-nt": "https://www.pamario-nt.lt/lt/kretinga/namai---",
    "ntseptynetas": "https://ntseptynetas.lt/",  # no public listing page found, see SITES.md
    "alginosnt": "https://alginosnt.lt/search?itemType=namas&search=Kretinga",
    "kretingosskelbimai": "https://www.kretingosskelbimai.lt/skelbimai/nekilnojamas-turtas/1.html",
    "infostatyba": "https://www.planuojustatyti.lt/statybos-leidimu-paieska",
    "tpdris": "https://www.tpdris.lt/lt/paieska",
}

# Per-preset browser options. realu loads its listing with a POST that only searches.
PRESET_ALLOW_POST = {"realu": ("getEstates",)}

OUT_ROOT = Path(__file__).resolve().parent / "out"
CHALLENGE_MARKERS = ("just a moment", "attention required", "cf-chl", "captcha", "access denied")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# What the spiders send (realestate_spiders/settings.py USER_AGENT): the plain fetch must
# look like Scrapy, not like the inspector's browser.
SPIDER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
FETCH_TIMEOUT_S = 30
# Pictures, video and fonts add requests to a site and nothing the recon reads.
SKIPPED_RESOURCES = {"image", "media", "font"}


class Throttle:
    """Keeps every request to one host at least `host_delay_s` after the previous one."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self.robots_delay: dict[str, float | None] = {}

    def wait(self, host: str) -> None:
        delay = host_delay_s(host, self.robots_delay.get(host))
        pause = self._last.get(host, 0) + delay - time.monotonic()
        if pause > 0:
            time.sleep(pause)
        self._last[host] = time.monotonic()


def fetch_robots(origin: str, throttle: Throttle) -> tuple[str, str]:
    """(robots.txt text, note). A missing or unreadable robots.txt allows everything."""
    host = urlsplit(origin).netloc
    throttle.wait(host)
    try:
        resp = requests.get(
            f"{origin}/robots.txt",
            headers={"User-Agent": SPIDER_USER_AGENT},
            timeout=FETCH_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        return "", f"robots.txt unreadable ({type(exc).__name__}), treated as allow"
    if resp.status_code != 200 or "<html" in resp.text[:500].lower():
        return "", f"robots.txt HTTP {resp.status_code} (no rules), treated as allow"
    return resp.text, f"robots.txt read ({len(resp.text)} bytes)"


def fetch_plain(url: str) -> dict:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": SPIDER_USER_AGENT, "Accept-Language": "lt,en;q=0.8"},
            timeout=FETCH_TIMEOUT_S,
        )
        return summarize_html("plain", resp.status_code, resp.url, resp.text)
    except requests.RequestException as exc:
        return summarize_html("plain", None, url, "", error=f"{type(exc).__name__}: {exc}"[:200])


def fetch_curl_cffi(url: str) -> dict:
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        return summarize_html("curl_cffi", None, url, "", error="curl_cffi not installed")
    try:
        resp = cffi_requests.get(
            url,
            impersonate="chrome",
            timeout=FETCH_TIMEOUT_S,
            headers={"Accept-Language": "lt,en;q=0.8"},
        )
        return summarize_html("curl_cffi", resp.status_code, resp.url, resp.text)
    except Exception as exc:  # noqa: BLE001 - curl_cffi raises its own error hierarchy
        error = f"{type(exc).__name__}: {exc}"[:200]
        return summarize_html("curl_cffi", None, url, "", error=error)

# Runs in the page. Returns every visible interactive element with a selector that matches it.
COLLECT_JS = r"""
() => {
  const CLICKABLE = 'a[href], button, input, select, textarea, summary, [role=button], [role=link],'
    + ' [role=tab], [role=menuitem], [role=checkbox], [onclick], [tabindex]:not([tabindex="-1"])';
  const NOISY_CLASS = /(^|-)(active|selected|open|hover|focus|show|hidden|disabled)$|\d{4,}|^js-|^ng-|^css-[a-z0-9]{5,}$/;
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const text = el => (el.innerText || el.value || el.getAttribute('aria-label') || el.title
    || el.getAttribute('placeholder') || el.querySelector('img[alt]')?.alt || '')
    .replace(/\s+/g, ' ').trim().slice(0, 120);
  const count = sel => { try { return document.querySelectorAll(sel).length } catch { return 0 } };
  const stableClasses = el => [...el.classList].filter(c => !NOISY_CLASS.test(c));
  const short = el => {
    const tag = el.tagName.toLowerCase();
    if (el.id && !/\d{4,}/.test(el.id)) return '#' + CSS.escape(el.id);
    const cls = stableClasses(el).slice(0, 2).map(c => '.' + CSS.escape(c)).join('');
    return tag + cls;
  };
  const selectorFor = el => {
    const tag = el.tagName.toLowerCase();
    // Per-item ids like #ads88091366 identify one listing, not the pattern: use them last.
    const stableId = el.id && !/\d{4,}/.test(el.id);
    if (stableId && count('#' + CSS.escape(el.id)) === 1) return '#' + CSS.escape(el.id);
    for (const attr of ['data-testid', 'data-test', 'data-qa', 'name', 'aria-label', 'title', 'type']) {
      const v = el.getAttribute(attr);
      if (v && v.length < 60) {
        const sel = `${tag}[${attr}="${CSS.escape(v)}"]`;
        if (count(sel) === 1) return sel;
      }
    }
    const cls = stableClasses(el).slice(0, 3);
    let sel = tag + cls.map(c => '.' + CSS.escape(c)).join('');
    if (count(sel) === 1) return sel;
    if (cls.length) return sel;  // repeated pattern (e.g. a.standard-list-item ×24) is the useful answer
    if (el.id && count('#' + CSS.escape(el.id)) === 1) return '#' + CSS.escape(el.id);
    const parent = el.parentElement && el.parentElement !== document.body ? short(el.parentElement) : '';
    if (parent && count(`${parent} > ${sel}`) >= 1) sel = `${parent} > ${sel}`;
    return sel;
  };
  const repeating = node => {
    const cls = stableClasses(node).slice(0, 2);
    if (!cls.length) return '';
    const sel = node.tagName.toLowerCase() + cls.map(c => '.' + CSS.escape(c)).join('');
    return count(sel) >= 4 ? sel : '';
  };
  const container = el => {
    // Nearest repeating ancestor (the card/row, e.g. article.listing-card); if none,
    // the link itself when it is the card (e.g. a.standard-list-item).
    let node = el.parentElement;
    for (let depth = 0; node && node !== document.body && depth < 5; depth++, node = node.parentElement) {
      const sel = repeating(node);
      if (sel) return sel;
    }
    return repeating(el);
  };
  const kindOf = el => {
    const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'a') return 'link';
    if (['select', 'textarea'].includes(tag)) return 'input';
    if (tag === 'input') return ['submit', 'button', 'reset', 'image'].includes(type) ? 'button' : 'input';
    return 'button';
  };
  const seen = new Set();
  const elements = [];
  for (const el of document.querySelectorAll(CLICKABLE)) {
    if (seen.has(el) || !visible(el) || elements.length >= 800) continue;
    seen.add(el);
    const selector = selectorFor(el);
    elements.push({
      kind: kindOf(el),
      tag: el.tagName.toLowerCase(),
      text: text(el),
      id: el.id || '',
      classes: [...el.classList],
      name: el.getAttribute('name') || '',
      type: el.getAttribute('type') || '',
      role: el.getAttribute('role') || '',
      href: el.getAttribute('href') || '',
      rel: el.getAttribute('rel') || '',
      aria_label: el.getAttribute('aria-label') || '',
      data_attrs: [...el.attributes].map(a => a.name).filter(n => n.startsWith('data-')),
      selector,
      matches: count(selector),
      container: el.tagName === 'A' ? container(el) : '',
    });
  }
  const forms = [...document.querySelectorAll('form')].filter(visible).map(f => ({
    selector: selectorFor(f),
    action: f.getAttribute('action') || '',
    method: (f.getAttribute('method') || 'get').toLowerCase(),
    fields: [...f.querySelectorAll('input, select, textarea')].slice(0, 40).map(i => ({
      name: i.getAttribute('name') || '', id: i.id || '', type: i.type || i.tagName.toLowerCase(),
      options: i.tagName === 'SELECT' ? [...i.options].slice(0, 25).map(o => o.text.trim()) : undefined,
    })),
  }));
  return { elements, forms };
}
"""


def _record_response(network: list[dict], response: Response) -> None:
    request = response.request
    if request.resource_type not in ("xhr", "fetch"):
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            return
    keys = ""
    if "json" in response.headers.get("content-type", ""):
        try:
            body = response.json()
            keys = ",".join(list(body)[:15]) if isinstance(body, dict) else type(body).__name__
        except Exception:  # noqa: BLE001 - body may be gone or not JSON; keys are optional
            keys = ""
    network.append(
        {"method": request.method, "status": response.status, "url": safe_url(response.url),
         "type": request.resource_type, "json_keys": keys}
    )


def inspect_page(
    page: Page,
    url: str,
    out_dir: Path,
    n: int,
    document_only: bool = False,
    allow_post: tuple[str, ...] = (),
) -> dict:
    network: list[dict] = []
    blocked: list[str] = []

    def guard(route: Route) -> None:
        request = route.request
        if is_forbidden_host(urlsplit(request.url).netloc):
            blocked.append(f"FORBIDDEN HOST {safe_url(request.url)}")
            route.abort()
        elif not request_allowed(request.method, request.url, allow_post):
            blocked.append(f"{request.method} {safe_url(request.url)}")
            route.abort()
        elif request.resource_type in SKIPPED_RESOURCES or (
            document_only and request.resource_type != "document"
        ):
            route.abort()
        else:
            route.continue_()

    page.route("**/*", guard)
    page.on("response", lambda r: _record_response(network, r))

    status = None
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        status = response.status if response else None
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:  # noqa: BLE001 - busy sites never go idle; what loaded is enough
            pass
        page.wait_for_timeout(1500)
    except Exception as exc:  # noqa: BLE001 - report the failure instead of crashing the run
        error = str(exc)[:300]
        return {"url": url, "status": status, "title": "", "error": error,
                "browser_fetch": summarize_html("playwright", status, url, "", error=error),
                "elements": [], "forms": [], "network": network, "blocked": blocked}

    title = page.title()
    html = page.content()
    browser_fetch = summarize_html("playwright", status, page.url, html)
    html_head = html[:5000].lower()
    challenge = next((m for m in CHALLENGE_MARKERS if m in title.lower() or m in html_head), "")
    if browser_fetch["wall"] == "none":
        challenge = ""  # a captcha widget on a normal listing page is not a block
    data = page.evaluate(COLLECT_JS)
    shot = f"page-{n:02d}.png"
    page.screenshot(path=str(out_dir / shot), full_page=True)
    page.unroute("**/*")

    return {
        "url": page.url,
        "requested_url": url,
        "status": status,
        "title": title,
        "challenge": challenge,
        "browser_fetch": browser_fetch,
        "screenshot": shot,
        "elements": data["elements"],
        "forms": data["forms"],
        "network": network,
        "blocked": blocked,
    }


def follow_targets(page_data: dict, base_url: str, limit: int) -> list[str]:
    """Same-site links from the most repeated card container: likely listing detail pages."""
    if limit <= 0:
        return []
    links = [e for e in page_data["elements"] if e["kind"] == "link"]
    host = urlsplit(base_url).netloc
    targets: list[str] = []
    for pattern in card_patterns(links):
        for item in links:
            if item["container"] != pattern["container"]:
                continue
            url = urljoin(base_url, item["href"])
            if urlsplit(url).netloc == host and url not in targets and not url.startswith("javascript"):
                targets.append(url)
            if len(targets) >= limit:
                return targets
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("target", help=f"preset ({', '.join(SITES)}) or a full URL")
    parser.add_argument("--follow", type=int, default=0, help="also open N listing-card links")
    parser.add_argument(
        "--document-only",
        action="store_true",
        help="browser loads only the HTML document (one request, no scripts); automatic for "
        "slow (Crawl-delay) hosts",
    )
    parser.add_argument(
        "--allow-post",
        action="append",
        default=[],
        metavar="URL_PART",
        help="let the browser send a POST whose URL contains this text (for portals that load "
        "the listing with a search POST; repeatable). Everything else non-GET stays blocked.",
    )
    args = parser.parse_args(argv)

    url = SITES.get(args.target, args.target)
    if not re.match(r"^https?://", url):
        parser.error(f"unknown site '{args.target}'. Presets: {', '.join(SITES)}")
    if is_forbidden_host(urlsplit(url).netloc):
        parser.error(f"{urlsplit(url).netloc} must never be requested")
    label = args.target if args.target in SITES else urlsplit(url).netloc.replace("www.", "")
    out_dir = OUT_ROOT / f"{label}-{datetime.now():%Y%m%d-%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)

    allow_post = tuple(args.allow_post) + PRESET_ALLOW_POST.get(args.target, ())
    throttle = Throttle()
    robots_cache: dict[str, str] = {}
    robots_notes: list[str] = []
    pages: list[dict] = []

    def gate(target: str) -> tuple[bool, str]:
        """robots.txt check for one URL (robots.txt itself is read once per host)."""
        parts = urlsplit(target)
        if parts.netloc not in robots_cache:
            text, note = fetch_robots(f"{parts.scheme}://{parts.netloc}", throttle)
            robots_cache[parts.netloc] = text
            robots_notes.append(f"{parts.netloc}: {note}")
            throttle.robots_delay[parts.netloc] = robots_crawl_delay(text)
        return robots_allows(robots_cache[parts.netloc], target), robots_notes[-1]

    def probe(target: str, n: int, browser_ctx) -> dict:
        """plain, curl_cffi, then the browser, one URL, throttled; verdict on top."""
        host = urlsplit(target).netloc
        allowed, _ = gate(target)
        if not allowed:
            fetch = {"verdict": "blocked", "wall": "none", "robots_disallowed": True,
                     "reason": "robots.txt disallows this URL, so it was not fetched", "results": []}
            return {"url": target, "status": None, "title": "", "elements": [], "forms": [],
                    "network": [], "blocked": [], "fetch": fetch}
        throttle.wait(host)
        plain = fetch_plain(target)
        throttle.wait(host)
        curl = fetch_curl_cffi(target)
        throttle.wait(host)
        slow = host_delay_s(host, throttle.robots_delay.get(host)) > 3
        page = browser_ctx.new_page()
        try:
            data = inspect_page(
                page, target, out_dir, n, args.document_only or slow, allow_post
            )
        finally:
            page.close()
        browser = data["browser_fetch"]
        verdict, wall, reason = decide_verdict(plain, curl, browser)
        results = [plain, curl, browser]
        for result in results:
            result.pop("hrefs", None)  # link sets are for the verdict only, not for pages.json
        data["fetch"] = {"verdict": verdict, "wall": wall, "reason": reason, "results": results}
        return data

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT, locale="lt-LT", viewport={"width": 1366, "height": 900}
        )
        print(f"Inspecting {url}", flush=True)
        pages.append(probe(url, 1, context))
        for i, target in enumerate(follow_targets(pages[0], pages[0]["url"], args.follow), 2):
            if is_forbidden_host(urlsplit(target).netloc):
                continue
            print(f"Following {target}", flush=True)
            pages.append(probe(target, i, context))
        browser.close()

    (out_dir / "pages.json").write_text(json.dumps(pages, ensure_ascii=False, indent=2))
    report = render_report(label, pages)
    report += "\n## robots.txt\n\n" + "\n".join(f"- {note}" for note in robots_notes) + "\n"
    (out_dir / "report.md").write_text(report)
    for p in pages:
        counts = {k: sum(1 for e in p["elements"] if e["kind"] == k) for k in ("button", "link", "input")}
        flag = f"  !! {p['challenge']}" if p.get("challenge") else ""
        flag += f"  !! {p['error']}" if p.get("error") else ""
        fetch = p["fetch"]
        print(f"  VERDICT {fetch['verdict']} (wall {fetch['wall']}): {fetch['reason']}")
        for r in fetch["results"]:
            print(f"    {r['method']:<10} HTTP {r['status']} wall {r['wall']} links {r['anchors']}"
                  f"{'  ' + r['error'] if r['error'] else ''}")
        print(f"  {p.get('status')} {p.get('title', '')[:60]!r}: {counts}, "
              f"{len(p['forms'])} forms, {len(p['network'])} xhr/json{flag}")
    host_path = Path("scrapers/recon/out") / out_dir.name
    print(f"Report: {host_path}/report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
