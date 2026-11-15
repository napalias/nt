# Recon v2: fetch verdicts for the NT sources

Scope: **houses for sale in Kretinga district**. Measured 2026-11-15 with `pnpm inspect <preset>`
(`scrapers/recon/inspect_site.py`), which fetches each URL with plain HTTP (the spiders' user agent),
`curl_cffi` (Chrome TLS impersonation) and headless Chromium, and compares what each one saw.

- **Verdict** is the cheapest method that still sees the listing page: `plain` < `curl_cffi` <
  `playwright`; `blocked` when none of them gets listing content or robots.txt forbids the URL. A
  plain/curl fetch counts as seeing the listing only if its HTML holds at least 90% of the same-site
  links the browser ended up with, so a JS shell wrapped in a big menu does not pass.
- **Wall** is `captcha`, `login`, `cookie_wall` or `none`.
- **Report** paths are local run output (`scrapers/recon/out/` is gitignored, so they exist only on the
  machine that ran the recon). Everything a spider author needs is copied below. Rerun with
  `pnpm inspect <preset>` (3 s between requests to a host, 10 s for `pamario-nt` and `ntseptynetas`;
  robots.txt is read first and a disallowed URL is not fetched).
- Gaps in the tool: it detects cards as repeated containers around `<a href>` links, so it cannot see
  cards that navigate with `onclick` (alginosnt). The card selectors below were checked by hand against
  the saved HTML or the browser DOM.
- The issue says 11 sites but lists 12 rows (the source registry from #4 had not landed). All 12 have a
  preset. `infostatyba` and `tpdris` presets (gov sources) are unchanged and not measured here.

## Summary

| Site | Verdict | Wall | Kretinga houses start URL | Card selector | Pagination | Report |
|---|---|---|---|---|---|---|
| aruodas | `blocked` | none (robots.txt) | `https://www.aruodas.lt/namai/kretingoje/` | not fetched | not fetched | `out/aruodas-20261115-213708/report.md` |
| realu | `playwright` | none | `https://www.realu.lt/nekilnojamasis-turtas/pirkti/namas/kretingos-r-sav` | `div.card-product` (link `a.info`) | `?page=N` (button `.pagination-nav`) | `out/realu-20261115-213707/report.md` |
| domoplius | `plain` | none | `https://domoplius.lt/skelbimai/namai-kotedzai-sodai/kretinga` | `article.listing-card` | `?page=N`, 20 per page | `out/domoplius-20261115-213708/report.md` |
| alio | `plain` | none | `https://www.alio.lt/paieska/kretingos-r-sav/?category_id=1433` | `a.main_a_c_b` | `/paieska/kretingos-r-sav/N/?category_id=1433` | `out/alio-20261115-213708/report.md` |
| skelbiu | `plain` (on this URL) | none | `https://www.skelbiu.lt/paieska/namai-kretingoje/` | `a.standard-list-item` | `/paieska/namai-kretingoje/N` | `out/skelbiu-20261115-213707/report.md` |
| rasknt | `plain` (index only) | login (detail) | `https://rasknt.lt/namai/kretinga/` | `article.listing` | none seen (9 cards) | `out/rasknt-20261115-213708/report.md` |
| capital | `plain` | none | `https://www.capital.lt/lt/nekilnojamas-turtas/pirkti/kretingos-r-sav/namai` | `a.realty-item` | none seen (12 cards) | `out/capital-20261115-213708/report.md` |
| meguvosbustas | `plain` | none | `https://meguvosbustas.lt/paieska` | `div.listing-wrapper > a` | `/paieska/puslapis-N` | `out/meguvosbustas-20261115-213708/report.md` |
| pamario-nt | `plain` | none | `https://www.pamario-nt.lt/lt/kretinga/namai---` | `a[href*="/lt/nta/"]` (unverified) | unverified | `out/pamario-nt-20261115-213707/report.md` |
| ntseptynetas | `plain` | none | none found | none | none | `out/ntseptynetas-20261115-213707/report.md` |
| alginosnt | `plain` | none | `https://alginosnt.lt/search?itemType=namas&search=Kretinga` | `div.item.desktop2` | `?page=N` (not exercised) | `out/alginosnt-20261115-213708/report.md` |
| kretingosskelbimai | `plain` | none | `https://www.kretingosskelbimai.lt/skelbimai/nekilnojamas-turtas/1.html` | `div.sk-item` | `/skelbimai/nekilnojamas-turtas/N.html` (1 to 9) | `out/kretingosskelbimai-20261115-213708/report.md` |

## Per site

### aruodas: blocked
robots.txt (read 2026-11-15) disallows `/namai/kretingoje/`, so the tool does not fetch it; that is the
finding. Per the epic's decision this goes to the human-assisted session (#15), not a bypass. The
earlier single-request probe saw a PerimeterX 403.

### realu: playwright
The listing page is server-rendered chrome with an empty results area. Cards are filled in by a script
that POSTs to `index.php?route=product/category/getEstates` and gets JSON back (`total, per_page,
current_page, last_page, view, view_projects, ...`; `view` is the cards' HTML). Plain and curl_cffi
each held 43% of the browser's links, so the verdict is `playwright`. The tool's read-only guard blocks
POSTs, so the preset runs with `--allow-post getEstates` (this one search POST and nothing else). A
spider can either render with Playwright or POST the same endpoint directly (needs the form fields
`op_slug`, `estate_type_slug`, `municipality_slug`, `page`; not tried). Detail links look like
`/parduodamas-namas-<place>-<sav>-<m2>-ploto-<floors>-realu<id>`. Slugs: `pirkti/namas/<sav>`, here
`kretingos-r-sav`; the page title confirms "Parduodami namai, Kretingos r. sav.".

### domoplius: plain
Inertia app: the server HTML carries the data as JSON in `div#app[data-page]`
(`props.properties` = 20 items, `props.pagination` = `{current_page, per_page: 20, total: 40,
last_page: 2}`), and the cards also render as `article.listing-card`. Page 2 is `?page=2` (checked,
20 cards). The tool's redirect for `?action_type=1` dropped that parameter; every card on page 1 was a
"parduodam..." (sale) slug, but the title says "pardavimas ir nuoma", so #9 should filter sale
explicitly. A second JSON API, `/api/listings/namai-kotedzai-sodai?...&bounds[...]`, backs the map.

### alio: plain
The starting evidence said Playwright; measured otherwise: plain, curl_cffi and the browser all saw the
same 159 links. `/nekilnojamas-turtas/kretingos-r-sav.html` mixes flats and plots; category 1433
(houses, farmsteads, garden houses for sale) at `/paieska/kretingos-r-sav/?category_id=1433` is the
Kretinga district houses list: 24 cards per page, all "Namas / Sodyba / Sodo namas ... Kretingos r. sav.".
Pagination links look like `/paieska/kretingos-r-sav/2/?category_id=1433`. Cards carry m2, plot (a),
year, heating, wall type, price and EUR/m2 in the link text.

### skelbiu: plain on the search URL, curl_cffi on the category URL
Two URLs, two outcomes. The category URL
`/skelbimai/nekilnojamasis-turtas/namai-kotedzai-sodai/` (nationwide, no Kretinga filter reachable
without the JS city picker) gave plain HTTP 403 with a captcha wall, curl_cffi 200 and browser 200
(run `out/skelbiu-20261115-212524`). The keyword search `/paieska/namai-kretingoje/` (found via web
search; the city id for the `cities=` filter was not found) returned plain 200 with 86 links,
identical to the browser. It is a keyword search, so results include land and unrelated ads (page 1
shows 23 cards): #12 must filter on the detail page or card text. Treat plain as likely but fall back to
curl_cffi, since the same host blocked plain on the other path. Alternative keyword URLs seen:
`/paieska/namai-kretingos-raj/`, `/paieska/parduodamas-namas-kretingos-r/`.

### rasknt: plain, but unusable as a source (needs-login)
The index page (`/namai/kretinga/`) is plain-fetchable: 9 `article.listing` cards, each with a source
label ("Kampas"), place title, price, EUR/m2, area, rooms. Every link on a card (both the "view" and
"analysis" buttons) points to `system.rasknt.lt` (a login/register redirect), which is never requested.
So the card has no link to the original ad, and the project rule is that every listing keeps a
`source_url`. It is also an aggregator of other portals (the label shows where it came from). Result:
park as `needs-login` unless #13 finds the original ad URL elsewhere. No pagination links seen.

### capital: plain
District-wide houses page `/lt/nekilnojamas-turtas/pirkti/kretingos-r-sav/namai` returned 12
`a.realty-item` cards, all houses; the city page `.../kretingos-r-sav/kretinga/namai` returns 5.
No pagination or "load more" at this size. The starting URL
`/lt/nekilnojamas-turtas/kretinga` mixes flats and plots. Cottages are separate categories
(`pirkti/kotedzai`, `sodybos-/-vasarnamiai`).

### meguvosbustas: plain
`/paieska` lists every object type (houses `/parduodami-namai/<region>/...`, plots `/sklypai/...`,
flats `/parduodami-butai/...`) in the Kretinga and Klaipeda districts, 9 `div.listing-wrapper > a`
cards per page, pages at `/paieska/puslapis-N`. Filter houses by href prefix `/parduodami-namai/`.
robots.txt is 13 bytes (no rules).

### pamario-nt: plain (Crawl-delay 10)
robots.txt asks for a 10 s delay (30 bytes). `/lt/kretinga/namai---` is the site's own "Kretinga houses"
link, but on 2026-11-15 it redirected straight to the single house's detail page
(`/lt/nta/37229-namas-kretinga`), meaning it holds one house. Because of that, the card selector
`a[href*="/lt/nta/"]` (detail links look like `/lt/nta/<id>-<type>-<place>`) and the pagination of a
multi-house result are unverified. `/lt/kretinga/namai-kretingos-rajone` has no listings. The browser
ran document-only on this host (one request per fetch).

### ntseptynetas: plain, no listing page
robots.txt: `Crawl-delay: 10`, nothing disallowed. The WordPress site (Divi theme, WPL plugin loaded)
exposes only Paslaugos, Komanda, NT verte, Partneriai and Kontaktai; no for-sale listing page was
found: `/property-listing/`, `/wp-sitemap.xml` and `/sitemap_index.xml` are all 404 and the home page
links to no object. Nothing to scrape; recommend dropping it from the spider list in #14 unless a listing
URL turns up.

### alginosnt: plain
`/nekilnojamas-turtas/namas` is nationwide (15 pages). A text search works over GET:
`/search?itemType=namas&search=Kretinga` returned 4 houses (Kretingale in the Klaipeda district among
them, since it is a substring match). Cards are `div.item.desktop2` (a `div.item.mobile2` duplicate sits
next to each) and are **not links**: the target is in an `onclick="location='.../nekilnojamas-turtas/skelbimas/<id>'"`
on the image, title and button, so a spider has to read the onclick or build the URL from the
`ID: <n>` span. Pagination on the nationwide list is `?page=N`; not exercised on the search.

### kretingosskelbimai: plain
`/skelbimai/nekilnojamas-turtas/N.html`, pages 1 to 9, `div.sk-item` cards (about 25 per page) with
detail URLs `/skelbimas/<id>-<slug>.html`. It is a local classifieds board and the category mixes ad
types (page 1 had building materials), so the house-ad filter is needed.

## Starting-table cells now resolved

| Site | Was | Is |
|---|---|---|
| aruodas | blocked, robots disallow | confirmed by the tool: robots disallows, not fetched |
| realu | URL unverified, plain | URL found; **playwright** (JS-rendered via a search POST) |
| domoplius | plain, embedded JSON | confirmed: plain, `data-page` JSON |
| alio | playwright, try curl_cffi | **plain** works |
| skelbiu | URL unknown, playwright | search URL found; plain works there, category URL needs curl_cffi |
| rasknt | plain, index only | confirmed, and cards have no original-ad link (system.rasknt.lt only) |
| capital | path unverified, plain | path found; plain |
| meguvosbustas | plain | confirmed |
| pamario-nt | plain, 10 s | confirmed; Kretinga page redirects to the single house |
| ntseptynetas | listing unverified, plain, 10 s | no listing page exists on the public site |
| alginosnt | plain | confirmed; cards use onclick, Kretinga via `search=` |
| kretingosskelbimai | plain, mixed ads | confirmed |
