"""Read-only site inspector: open a portal in headless Chromium and map what can be clicked.

    pnpm inspect domoplius              # a preset (see SITES)
    pnpm inspect https://... --follow 2 # any URL, then 2 listing-card links

Runs inside the scraper_worker container (Playwright + Chromium already installed).
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

from playwright.sync_api import Page, Response, Route, sync_playwright

from recon.analyze import card_patterns, is_read_only, render_report, safe_url

SITES = {
    "domoplius": "https://domoplius.lt/skelbimai/namai-kotedzai-sodai?action_type=1&page_nr=1",
    "skelbiu": "https://www.skelbiu.lt/skelbimai/nekilnojamasis-turtas/namai-kotedzai-sodai/",
    "aruodas": "https://www.aruodas.lt/namai/puslapis/1/",
    "alio": "https://www.alio.lt/nekilnojamas-turtas/namai-kotedzai",
    "infostatyba": "https://www.planuojustatyti.lt/statybos-leidimu-paieska",
    "tpdris": "https://www.tpdris.lt/lt/paieska",
}

OUT_ROOT = Path(__file__).resolve().parent / "out"
PAGE_DELAY_S = 3  # same politeness floor as the spiders (DOWNLOAD_DELAY >= 3 s)
CHALLENGE_MARKERS = ("just a moment", "attention required", "cf-chl", "captcha", "access denied")
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

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


def inspect_page(page: Page, url: str, out_dir: Path, n: int) -> dict:
    network: list[dict] = []
    blocked: list[str] = []

    def guard(route: Route) -> None:
        if is_read_only(route.request.method):
            route.continue_()
        else:
            blocked.append(f"{route.request.method} {safe_url(route.request.url)}")
            route.abort()

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
        return {"url": url, "status": status, "title": "", "error": str(exc)[:300],
                "elements": [], "forms": [], "network": network, "blocked": blocked}

    title = page.title()
    html_head = page.content()[:5000].lower()
    challenge = next((m for m in CHALLENGE_MARKERS if m in title.lower() or m in html_head), "")
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
    args = parser.parse_args(argv)

    url = SITES.get(args.target, args.target)
    if not re.match(r"^https?://", url):
        parser.error(f"unknown site '{args.target}'. Presets: {', '.join(SITES)}")
    label = args.target if args.target in SITES else urlsplit(url).netloc.replace("www.", "")
    out_dir = OUT_ROOT / f"{label}-{datetime.now():%Y%m%d-%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)

    pages: list[dict] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=USER_AGENT, locale="lt-LT", viewport={"width": 1366, "height": 900}
        )
        page = context.new_page()
        print(f"Inspecting {url}", flush=True)
        pages.append(inspect_page(page, url, out_dir, 1))
        for i, target in enumerate(follow_targets(pages[0], pages[0]["url"], args.follow), 2):
            time.sleep(PAGE_DELAY_S)
            print(f"Following {target}", flush=True)
            page.close()
            page = context.new_page()
            pages.append(inspect_page(page, target, out_dir, i))
        browser.close()

    (out_dir / "pages.json").write_text(json.dumps(pages, ensure_ascii=False, indent=2))
    (out_dir / "report.md").write_text(render_report(label, pages))
    for p in pages:
        counts = {k: sum(1 for e in p["elements"] if e["kind"] == k) for k in ("button", "link", "input")}
        flag = f"  !! {p['challenge']}" if p.get("challenge") else ""
        flag += f"  !! {p['error']}" if p.get("error") else ""
        print(f"  {p.get('status')} {p.get('title', '')[:60]!r}: {counts}, "
              f"{len(p['forms'])} forms, {len(p['network'])} xhr/json{flag}")
    host_path = Path("scrapers/recon/out") / out_dir.name
    print(f"Report: {host_path}/report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
