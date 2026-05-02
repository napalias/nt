# BUILD_PLAN.md

Phased build plan optimized for Claude Code sessions. Each **task** is sized to one Claude Code session. Phases marked **PARALLEL** can be split across worktrees and run simultaneously.

## Phase 0 — Bootstrap (1 session, must be done first)

**Goal**: `docker compose up` starts a healthy stack with empty Django + empty SvelteKit + empty Scrapy.

### Task 0.1 — Initialize repo

- [ ] `git init`, copy `.env.example` to `.env`
- [ ] Add a `.gitignore` covering Python, Node, Django, OS junk, `.env`, `__pycache__`, `node_modules`, `dist`, `.venv`
- [ ] `docker compose pull` to prefetch base images

### Task 0.2 — Scaffold Django

```bash
docker compose run --rm backend django-admin startproject config .
docker compose run --rm backend python manage.py startapp listings apps/listings
docker compose run --rm backend python manage.py startapp geo apps/geo
docker compose run --rm backend python manage.py startapp search apps/search
```

- [ ] Split settings into `config/settings/{base,dev,prod}.py`
- [ ] Configure `INSTALLED_APPS`: add `django.contrib.gis`, `django_celery_beat`, `apps.listings`, `apps.geo`, `apps.search`
- [ ] Configure `DATABASES` to use `django.contrib.gis.db.backends.postgis` and read `DATABASE_URL` via `django-environ`
- [ ] Add `config/celery.py` with broker = `CELERY_BROKER_URL`
- [ ] Add a healthcheck endpoint at `/health/` that returns `{"ok": true}`
- [ ] `docker compose exec backend python manage.py migrate` — migrations apply, no errors
- [ ] **Acceptance**: `curl http://localhost:8000/health/` → `{"ok":true}`; `curl http://localhost:8000/admin/` → login page

### Task 0.3 — Scaffold SvelteKit

```bash
docker compose run --rm frontend sh -c "pnpm create svelte@latest . --template skeleton --types ts --eslint --prettier --no-playwright --no-vitest"
```

- [ ] Install Tailwind, MapLibre GL, openapi-typescript
- [ ] Configure `vite.config.ts` to expose dev server on `0.0.0.0:5173`
- [ ] Add a placeholder `/` route showing "hello, real estate" + a button hitting `/health/` on the backend
- [ ] **Acceptance**: `http://localhost:5173` renders; button click successfully hits backend through CORS

### Task 0.4 — Scaffold Scrapy

```bash
docker compose run --rm scrapers scrapy startproject realestate_spiders .
```

- [ ] Configure `settings.py` per CLAUDE.md conventions (DOWNLOAD_DELAY=3, CONCURRENT_REQUESTS_PER_DOMAIN=1, HTTPCACHE_ENABLED=True)
- [ ] Set up Django ORM bootstrap in a `django_setup.py` so pipelines can `from apps.listings.models import Listing`
- [ ] Add a no-op spider `test` that yields one fake item, to prove the pipeline runs
- [ ] **Acceptance**: `docker compose run --rm scrapers scrapy crawl test` exits 0

---

## Phase 1 — Listing model + admin (1 session)

**Goal**: A `Listing` model with PostGIS point field, full Django admin, factories for tests.

- [ ] Create `apps/listings/models.py` per the schema in CLAUDE.md / project plan
- [ ] Register in `apps/listings/admin.py` with `OSMGeoAdmin` so locations are editable on a map
- [ ] Add `apps/listings/factories.py` with `model_bakery` recipes for tests
- [ ] Migration: `makemigrations` + `migrate`
- [ ] Seed 20 fake listings via a `seed_listings` management command (`python manage.py seed_listings 20`)
- [ ] **Acceptance**: 20 listings visible in admin with map widget; `Listing.objects.filter(location__dwithin=(Point(25.27, 54.69, srid=4326), D(km=5))).count()` returns sane number from `shell_plus`

---

## Phase 2 — Search API (1 session)

**Goal**: Working `/api/search` endpoint with radius + filters.

- [ ] Wire Django Ninja: `config/urls.py` mounts an `api` instance at `/api/`
- [ ] Implement `apps/search/api.py` with `GET /api/search` accepting `lat`, `lng`, `radius_m`, `min_price`, `max_price`, `rooms`, `property_type`, `listing_type`
- [ ] Returns up to 200 results sorted by distance, with `distance_m` annotation
- [ ] Add `GET /api/geocode?q=...` that proxies Nominatim and returns `{lat, lng, display_name}`
- [ ] Add tests in `apps/search/tests/test_api.py` using factories
- [ ] **Acceptance**: `curl 'http://localhost:8000/api/search?lat=54.6872&lng=25.2797&radius_m=5000'` returns JSON list; OpenAPI visible at `/api/docs`

---

## Phase 3 — PARALLEL: Frontend + First Scraper

These two are independent. Spin up two worktrees and run them simultaneously.

### Phase 3a — Frontend search UI (worktree: `feature/frontend-search`)

- [ ] Generate types: `openapi-typescript http://backend:8000/api/openapi.json -o src/lib/api/schema.d.ts`
- [ ] Build a typed fetch wrapper in `src/lib/api/client.ts`
- [ ] Route `/search`:
  - Search input at top → calls `/api/geocode` → centers map
  - Map (left, MapLibre + OSM/Protomaps tiles) with markers for each listing
  - List (right) bound to same data, scroll-syncs with map
  - URL holds `?lat=&lng=&radius=&minPrice=&...`
  - Filter sidebar: price range, rooms, property type, listing type
- [ ] Marker click → highlights list item; list hover → highlights marker
- [ ] **Acceptance**: search "Vilnius centras" → map centers on Vilnius, listings within 5 km render as both markers and list items

### Phase 3b — Domoplius scraper (worktree: `feature/scraper-domoplius`)

Easiest source. Establishes the pattern for the others.

- [ ] `realestate_spiders/items.py`: `ListingItem` with all fields from the model
- [ ] `realestate_spiders/spiders/domoplius.py`: spider that walks index pages and parses listing detail pages
- [ ] Pipelines (in order):
  1. `ValidatePipeline` — drops items missing required fields
  2. `NormalizePipeline` — strips, converts types, computes `content_hash`
  3. `GeocodePipeline` — calls Nominatim, drops items that can't be located
  4. `DjangoWritePipeline` — `update_or_create` against `Listing` model
- [ ] Set `HTTPCACHE_DIR=/app/.scrapy_cache` so re-runs are fast in dev
- [ ] Add `apps/listings/tasks.py::run_spider(name)` Celery task that shells out to `scrapy crawl <name>` so it can be triggered from beat
- [ ] **Acceptance**: `docker compose run --rm scrapers scrapy crawl domoplius -a max_pages=3` ingests ≥30 listings into Postgres with valid coordinates

---

## Phase 4 — Coverage scrapers (1–2 sessions)

**Goal**: Two more sources working, deduplication clustering job.

- [ ] `skelbiu` spider (similar pattern to domoplius)
- [ ] `aruodas` spider — this one needs `scrapy-playwright` + `curl_cffi` because of Cloudflare:
  - Configure middleware to use `curl_cffi` for index pages with browser TLS fingerprint
  - Fall back to Playwright (chromium) only on detail pages that require JS
  - Aggressive `DOWNLOAD_DELAY` (5s+) and rotating user agents
- [ ] Deduplication: nightly Celery task `cluster_listings` that links listings within 50m + same area (±2 sqm) + same room count + price within 5%
- [ ] **Acceptance**: All three spiders run successfully via beat; admin shows clustered groups for any cross-posted listings

---

## Phase 5 — Auth + saved searches (1 session)

- [ ] Django allauth, email-based signup
- [ ] `SavedSearch` model: user + filter params + last_notified_at
- [ ] Celery task `notify_saved_searches` (every 30 min) — for each saved search, find listings created since `last_notified_at` matching the filters, send a digest email via MailHog
- [ ] Frontend: signup/login pages, "save this search" button on `/search`
- [ ] **Acceptance**: save a search; trigger a fresh scrape; check MailHog at :8025 — email arrives with new matches

---

## Phase 6 — Production-readiness (1 session)

- [ ] Add Sentry to backend + frontend (free tier)
- [ ] Add Healthchecks.io ping at the end of every Celery scheduled task
- [ ] Rate limit `/api/search` (django-ratelimit, 60/min/IP)
- [ ] Add `nightly_db_backup` Celery task → `pg_dump` to local volume (production will redirect to object storage)
- [ ] Add a `prod.yml` docker-compose overlay (no source mounts, gunicorn instead of runserver, built SvelteKit with `adapter-node`)
- [ ] **Acceptance**: `docker compose -f docker-compose.yml -f prod.yml up` runs without source mounts and serves a built bundle

---

## Phase 7 — PARALLEL: Cadastre + Developers

This is where the app stops being just a listing aggregator. **Read `GOV_DATA_SOURCES.md` first.**

### Phase 7a — Cadastre + restrictions from GeoPortal (worktree: `feature/cadastre`)

- [ ] Add `apps/cadastre/` with models: `CadastralPlot`, `HeritageObject`, `SpecialLandUseCondition`
- [ ] Install `OWSLib` for WFS access
- [ ] `apps/cadastre/services/geoportal.py`: client that calls GeoPortal WFS GetFeature with bounding box, returns parsed features
- [ ] Celery task `sync_cadastre_for_bbox` — used on demand when the user pans the map outside currently-cached areas
- [ ] Strategy: lazy population. Don't try to ingest all of Lithuania day one. Sync per bounding box on first view, cache in DB.
- [ ] Layer endpoints: `/api/layers/cadastre?bbox=...`, `/api/layers/heritage?bbox=...`, `/api/layers/restrictions?bbox=...` returning GeoJSON
- [ ] **Acceptance**: pan map over Vilnius → cadastral plots fill in as polygon overlay; click plot → sidebar shows purpose + area

### Phase 7b — Developers from JAR open data (worktree: `feature/developers`)

- [ ] Add `apps/developers/` with `Developer` model
- [ ] One-shot management command `import_jar_dump <path-to-csv>`:
  - Parses Lithuanian JAR open data export
  - Filters NACE codes 41.10, 41.20, 68.10, 68.20, 68.31
  - Geocodes registered address via Nominatim
  - Bulk inserts ~thousands of records
- [ ] Celery monthly task to fetch the latest JAR dump and re-import (idempotent on `company_code`)
- [ ] Optional enrichment: scrape Sodra employee count history per company (slower, run as background task)
- [ ] API endpoint `/api/developers?bbox=...` returning developers with registered offices in bbox (their actual project areas come in Phase 8 via permits)
- [ ] **Acceptance**: ~5–15k developers in DB; admin search by name works; map shows pins for developers in viewport

---

## Phase 8 — Building permits from Infostatyba (1–2 sessions)

**Goal**: Active and recent building permits visible on the map, linked to developers and cadastral plots.

- [ ] Add `apps/permits/` with `BuildingPermit` model
- [ ] Reverse-engineer the planuojustatyti.lt search interface (POST params, paging, JSON responses where possible)
- [ ] `scrapers/realestate_spiders/spiders/infostatyba.py`:
  - Walks search results by date range (start with last 12 months)
  - Parses each permit detail page
  - Yields `PermitItem` to pipeline
- [ ] Pipelines:
  - `MatchPlotPipeline` — joins on cadastral_number to find geometry from `CadastralPlot`
  - `MatchDeveloperPipeline` — fuzzy-matches applicant name to `Developer.company_code` via JAR (most permits include the applicant's legal entity code — use that when present)
  - `WritePipeline`
- [ ] Backfill 12 months of permits, then daily incremental
- [ ] API endpoint `/api/permits?bbox=...&issued_after=...&status=...`
- [ ] Frontend: permits as a toggleable layer, color-coded by status (issued/in-progress), click → sidebar shows applicant (linkable to developer profile), description, dates, source link to Infostatyba
- [ ] **Acceptance**: 10k+ permits ingested, 80%+ matched to plots, 60%+ matched to developers; clicking permit shows developer link; clicking developer shows all their permits

---

## Phase 9 — Territorial planning from TPDRIS (2 sessions)

**Goal**: Approved planning documents visible on the map, with key facts (height, density, allowed uses) extracted via LLM.

This is the highest-value, highest-effort phase. Split into two sessions.

### Phase 9a — TPDRIS scraper + document storage

- [ ] Add `apps/planning/` and `apps/documents/` with `PlanningDocument` and `Document` models
- [ ] `scrapers/realestate_spiders/spiders/tpdris.py`:
  - Walks search by status=`approved`, paginated
  - Parses metadata (title, type, status, municipality, dates, geographic scope text)
  - Downloads attached PDFs/shapefiles to local volume (later → object storage)
  - When a shapefile is attached, parse its boundary into `PlanningDocument.boundary` (use `fiona` or `geopandas`)
- [ ] When no shapefile, leave boundary null and flag for manual review (don't guess)
- [ ] Pipeline writes `Document` records linked to `PlanningDocument`
- [ ] **Acceptance**: 1000+ approved planning documents in DB, 60%+ with parsed boundary geometry

### Phase 9b — LLM extraction of plan facts

- [ ] `apps/planning/services/extract.py`: function that takes a `PlanningDocument` + its PDFs and runs LLM extraction
- [ ] Pipeline:
  1. Extract text from PDFs with `pymupdf` (text-based) or `pytesseract` (scanned)
  2. Build a prompt asking for: `allowed_uses` (list), `max_height_m`, `max_floors`, `max_density`, `parking_requirements` (string), `extraction_confidence` (0–1)
  3. Call Anthropic API (you already have prompt-engineering experience from BNSN)
  4. Save structured data to the `PlanningDocument`
- [ ] Run as Celery task per document; rate-limit to avoid API spikes
- [ ] Add admin action "Re-extract" so you can manually retrigger
- [ ] API: `/api/planning?bbox=...` returns documents with boundary intersecting bbox + their extracted facts
- [ ] Frontend: planning documents as a toggleable polygon layer, color by document type; click → sidebar shows extracted facts + link to original PDF + low-confidence flag
- [ ] **Acceptance**: pick any address in Vilnius, see "covered by detailed plan X (approved 2019), max 3 floors, residential + commercial allowed"; confidence below 0.6 shows a "verify with original document" warning

---

## Phase 10 — Unified search (1 session)

**Goal**: One search call returns all layers. Map and sidebar render them together.

- [ ] Refactor `/api/search` to return the multi-layer response described in `GOV_DATA_SOURCES.md`
- [ ] Frontend: redesign `/search` to have a left sidebar with layer toggles + counts, map in the middle, detail panel on the right that updates based on what you click
- [ ] Add a "Property Report" view at `/property/<plot-id>` that shows everything we know about a single plot:
  - Cadastral info (size, purpose)
  - Active listings on this plot
  - Building permits on this plot
  - Planning documents covering this plot (with extracted limits)
  - Restrictions (heritage, water protection, special land use)
  - Active developers operating in the immediate area
- [ ] **Acceptance**: search "Žvėrynas, Vilnius" → see listings + 5 permits + 2 planning docs + heritage zone overlay; click a property → full Property Report assembles everything

---

## Phase 11 — Optional enrichments

- [ ] Sodra integration for developer employee counts
- [ ] AAA scraper for environmental impact assessments
- [ ] KVR (cultural heritage) detailed metadata beyond GeoPortal layer
- [ ] Paid Registrų centras NTR integration for ownership + transactions (premium tier)
- [ ] Saved property reports / PDF export

---

## Working with Claude Code on this project

### Recommended worktree layout (Phase 3 onward)

```
realestate/                      # main worktree (master)
realestate-worktrees/
├── feature-frontend-search/     # phase 3a
├── feature-scraper-domoplius/   # phase 3b
├── feature-scraper-aruodas/     # phase 4
└── feature-auth/                # phase 5
```

Use your existing `br()` / `b:w` / `b:s` workflow. Each worktree gets its own Claude Code instance.

### Per-session prompt template

When starting a new session, paste:

> Read `CLAUDE.md` and `BUILD_PLAN.md`. I'm working on **Task X.Y — [name]**. Don't pull scope from later tasks. Verify acceptance criteria before declaring done.

### Useful `allowedTools` for this project

For backend / migration sessions, allow:
```
Bash(docker compose *), Bash(pnpm *), Edit, Write, Read, Glob, Grep
```

For scraper sessions, additionally allow:
```
Bash(scrapy *), Bash(curl *)
```

### Verification commands Claude Code should run before declaring a task done

```bash
# Backend
docker compose exec backend python manage.py check
docker compose exec backend python manage.py migrate --check
docker compose exec backend pytest        # once tests exist
docker compose exec backend ruff check .

# Frontend
docker compose exec frontend pnpm check
docker compose exec frontend pnpm exec eslint .

# Scrapers
docker compose run --rm scrapers ruff check .
docker compose run --rm scrapers scrapy check
```

---

## What "done" looks like (top of mind)

After Phase 4 you have the listing aggregator MVP:
- A working app at `http://localhost:5173`
- Three real Lithuanian sources scraped on schedule
- Search any address → see real listings within 5 km on a map
- Cross-source duplicates collapsed into clusters

After Phase 10 you have the property research tool:
- Listings + permits + developers + planning docs + restrictions on one map
- Click any plot → full Property Report with everything we know
- LLM-extracted facts from territorial plans ("max 3 floors, residential + commercial")
- Differentiator: nothing in Lithuania today combines these layers

Phases 5–6 are quality-of-life; Phase 11 is optional enrichments.
