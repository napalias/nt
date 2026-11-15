# NT Paieška — Real Estate Research for Lithuania

Property research tool that combines listings from multiple portals with government data (permits, planning, cadastre, restrictions) and AI evaluation on a single map. Built for house hunting in the Kretinga area.

## What it does

Search any address in Lithuania → see within 5 km:
- **Listings** from Domoplius, Aruodas, Skelbiu (scraped daily)
- **Building permits** from Infostatyba
- **Territorial plans** from TPDRIS with AI-extracted facts (max floors, allowed uses)
- **Cadastral plots** from GeoPortal + Kretinga local GIS
- **Heritage zones** and special land use restrictions
- **Developers** from JAR open data

AI features:
- Each listing classified against your criteria (match/review/skip)
- Like/dislike feedback → system learns your preferences
- Cross-portal duplicate detection
- Marketing fluff cleaned from descriptions
- LLM extraction of planning document facts

## Quick start

```bash
pnpm start          # starts Docker if needed, boots every container, migrates, prints URLs
pnpm stop           # stops every container (data volumes are kept)
pnpm restart        # stop, then start
pnpm start --build  # rebuild images first (after Dockerfile / dependency changes)
pnpm logs           # follow logs of every service
pnpm status         # list containers
```

The first boot builds images and imports Nominatim's Lithuania extract (~5–10 min). `pnpm start` creates `.env` from `.env.example` if it's missing.

Optional fake data: `docker compose exec backend python manage.py seed_listings 20`

## URLs

| Service | URL |
|---------|-----|
| Frontend | http://localhost:5173 |
| Scan history | http://localhost:5173/scans |
| Backend API | http://localhost:8000/api |
| API docs | http://localhost:8000/api/docs |
| Admin | http://localhost:8000/admin |
| Mailpit | http://localhost:8025 |

## Scanning websites

Scans run automatically once the stack is up: Celery beat queues them daily (Infostatyba 05:00, Domoplius 06:00, Skelbiu 07:00, Aruodas 08:00, Alio 08:30, TPDRIS Sundays 04:00) and the `scraper_worker` container runs them one at a time. Watch with `pnpm logs scraper_worker`.

Every run is recorded (pages, listings found, new, updated, dropped and why). See them at http://localhost:5173/scans (nav: **Nuskaitymai**). Click a run to see its drop reasons and the new listings it brought in.

Scan on demand:

```bash
pnpm scrape              # all listing portals, 2 pages each
pnpm scrape alio 5       # one spider, 5 pages
```

Map a site before writing or fixing a spider (read-only headless Chromium in the scraper container):

```bash
pnpm inspect domoplius             # presets: domoplius, skelbiu, aruodas, alio, infostatyba, tpdris
pnpm inspect https://... --follow 2  # any URL, plus 2 listing-card links
```

Writes `scrapers/recon/out/<site>-<time>/report.md` (buttons, ids, classes, working selectors, forms, listing-card containers, pagination, XHR/JSON endpoints, bot-challenge flag), `pages.json` and screenshots. Every non-GET request is blocked, so nothing on the site can be submitted.

Raw spider runs:

```bash
docker compose run --rm scrapers scrapy crawl domoplius -a max_pages=2
docker compose run --rm scrapers scrapy crawl skelbiu -a max_pages=2
docker compose run --rm scrapers scrapy crawl aruodas -a max_pages=1
docker compose run --rm scrapers scrapy crawl infostatyba -a max_pages=5
docker compose run --rm scrapers scrapy crawl tpdris -a max_pages=5
```

## AI classification

```bash
# Classify all unclassified listings
curl -X POST http://localhost:8000/api/classifier/classify/batch \
  -H 'Content-Type: application/json' -d '{"limit": 50}'

# Give feedback
curl -X POST http://localhost:8000/api/classifier/feedback/1 \
  -H 'Content-Type: application/json' \
  -d '{"feedback_type": "like", "reason": "didelis sklypas, geoterminis sildymas"}'

# View learned preferences
curl http://localhost:8000/api/classifier/preferences
```

## Stack

| Layer | Choice |
|-------|--------|
| Scraping | Scrapy + scrapy-playwright + curl_cffi |
| Backend | Django 5 + GeoDjango + Django Ninja |
| Database | PostgreSQL 16 + PostGIS 3.4 |
| AI | Claude API (Sonnet for classification, Haiku for dedup/cleanup) |
| Frontend | SvelteKit (Svelte 5) + MapLibre GL + Tailwind |
| Scheduling | Celery + Redis + django-celery-beat |
| Geocoding | Nominatim (Lithuania extract) |

## Project structure

```
backend/apps/
├── listings/     # Listing model, admin, search, seed command
├── search/       # Search API, geocode, saved searches
├── classifier/   # AI classification, feedback, dedup, cleanup
├── cadastre/     # GeoPortal WFS + Kretinga ArcGIS integration
├── developers/   # JAR open data import
├── permits/      # Infostatyba building permits
├── planning/     # TPDRIS territorial planning + LLM extraction
├── documents/    # PDF storage for planning docs
└── geo/          # Geocoding utilities

scrapers/realestate_spiders/spiders/
├── listings/domoplius.py
├── listings/skelbiu.py
├── listings/aruodas.py
├── infostatyba.py
└── tpdris.py

frontend/src/routes/
├── +page.svelte          # Dashboard
├── search/+page.svelte   # Map + listings + filters
├── preferences/          # AI learned preferences
├── searches/             # Saved searches
└── property/[id]/        # Property report
```

## Production

```bash
docker compose -f docker-compose.yml -f prod.yml up -d
```

See `CLAUDE.md` for full conventions and `BUILD_PLAN.md` for the build sequence.
