# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A property research / due diligence web app for Lithuania. Given a location (address, city, district), it shows everything within a 5 km radius across multiple layers:

1. **Real estate listings** — aggregated from Aruodas, Domoplius, Skelbiu, etc.
2. **Real estate developers** — companies active in the area, their other projects
3. **Building permits** — what's being built, by whom, when (from Infostatyba)
4. **Territorial planning documents** — master plans, detailed plans, zoning, height limits (from TPDRIS)
5. **Cadastral data** — plot boundaries, purpose, area (from GeoPortal NTKR)
6. **Restrictions** — cultural heritage zones, water protection, special land use conditions

The differentiator vs. existing Lithuanian sites: nobody combines listings with permits + planning + restrictions on one map. Aruodas only shows listings; gov sites are siloed and have bad UX.

**See `GOV_DATA_SOURCES.md` for the full breakdown of Lithuanian data sources** and how each feeds into the app.

**See `NT_SOURCES.md` for the list of Lithuanian real estate portals, local agencies, and brokers.** Reference this file when adding new spider targets, deciding which sites to scrape, or discussing regional coverage.

## Stack — non-negotiable choices

| Layer | Choice |
|-------|--------|
| Scraping | **Scrapy** (Python) with `scrapy-playwright` + `curl_cffi` for hard sites |
| Backend | **Django 5 + GeoDjango + Django Ninja** |
| Database | **PostgreSQL 16 + PostGIS 3.4** |
| Queue | **Celery + Redis** (with `django-celery-beat` for scheduling) |
| Frontend | **SvelteKit** (Svelte 5 syntax) + **MapLibre GL** + Tailwind |
| Geocoder | **Nominatim** (self-hosted, Lithuania extract only for local) |
| Local dev | **Docker Compose** — everything runs in containers |
| Package mgmt | `uv` for Python, `pnpm` for Node |

Do not propose alternatives unless the user asks. The choices above were made deliberately for this domain (heavy scraping + heavy geo queries).

## Repository layout

```
realestate/
├── docker-compose.yml          # local dev orchestration
├── .env / .env.example
├── CLAUDE.md                   # this file
├── BUILD_PLAN.md               # phased build sequence
├── GOV_DATA_SOURCES.md         # Lithuanian gov data sources reference
├── backend/                    # Django project
│   ├── config/                 # settings, celery, urls, wsgi
│   ├── apps/
│   │   ├── listings/           # Listing model, admin, API (Phase 1–2)
│   │   ├── geo/                # geocoding utilities, clustering
│   │   ├── search/             # multi-layer search + filter logic
│   │   ├── developers/         # Companies, NACE filter, projects link (Phase 7)
│   │   ├── permits/            # Building permits from Infostatyba (Phase 8)
│   │   ├── planning/           # TPDRIS documents, polygons, extracted facts (Phase 9)
│   │   ├── cadastre/           # NTKR plots, SŽNS, heritage, environmental (Phase 7)
│   │   └── documents/          # PDF blob storage, indexing, full-text search
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── manage.py
├── scrapers/                   # Scrapy project (separate Python project)
│   ├── realestate_spiders/
│   │   ├── spiders/
│   │   │   ├── listings/       # aruodas, domoplius, skelbiu (Phase 3–4)
│   │   │   ├── infostatyba.py  # building permits (Phase 8)
│   │   │   └── tpdris.py       # planning docs (Phase 9)
│   │   ├── pipelines/
│   │   ├── items.py
│   │   ├── middlewares.py
│   │   └── settings.py
│   ├── pyproject.toml
│   ├── Dockerfile
│   └── scrapy.cfg
└── frontend/                   # SvelteKit
    ├── src/
    │   ├── routes/
    │   ├── lib/
    │   │   ├── api/            # generated from OpenAPI
    │   │   └── map/            # MapLibre + layer toggle controls
    │   └── app.html
    ├── package.json
    └── svelte.config.js
```

## Common commands

All run from repo root.

```bash
# Bring up everything
docker compose up -d

# Bring up everything and watch logs
docker compose up

# View logs for one service
docker compose logs -f backend

# Django management
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py createsuperuser
docker compose exec backend python manage.py shell_plus

# Run a Scrapy spider once (manual profile, not auto-started)
docker compose run --rm scrapers scrapy crawl domoplius
docker compose run --rm scrapers scrapy crawl domoplius -a max_pages=2  # debug run

# Frontend
docker compose exec frontend pnpm check
docker compose exec frontend pnpm exec openapi-typescript http://backend:8000/api/openapi.json -o src/lib/api/schema.d.ts

# Run backend tests
docker compose exec backend pytest
docker compose exec backend pytest apps/listings/tests/test_models.py  # single file
docker compose exec backend pytest -k test_search_radius               # single test by name

# Lint + format (Python)
docker compose exec backend ruff check .
docker compose exec backend ruff format .

# Database access
docker compose exec postgres psql -U postgres -d realestate

# Reset everything (nuclear)
docker compose down -v
```

## URLs (local)

| Service | URL |
|---------|-----|
| Frontend | http://localhost:5173 |
| Backend API | http://localhost:8000/api |
| Backend admin | http://localhost:8000/admin |
| OpenAPI docs | http://localhost:8000/api/docs |
| Nominatim | http://localhost:7070 |
| MailHog UI | http://localhost:8025 |
| Postgres | localhost:5432 (user: postgres / pass: postgres) |

## Environment variables

The `.env` file (copied from `.env.example`) must define:

| Variable | Example | Notes |
|----------|---------|-------|
| `DJANGO_SETTINGS_MODULE` | `config.settings.dev` | Always `dev` locally |
| `DATABASE_URL` | `postgis://postgres:postgres@postgres:5432/realestate` | Note `postgis://` scheme, not `postgres://` |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | |
| `NOMINATIM_URL` | `http://nominatim:8080` | Internal Docker hostname, **not** `localhost:7070` |
| `SECRET_KEY` | (any random string) | Django secret key |
| `DEBUG` | `True` | |

## Testing

- Backend uses **pytest** with `pytest-django` and `model-bakery` for factories
- Test files go in `apps/<app>/tests/` (not a top-level `tests/` directory)
- Use `baker.make(Listing)` to create model instances in tests — don't write manual factory functions
- `conftest.py` at `backend/conftest.py` for shared fixtures (db, client, etc.)

## Coding conventions

### Python (backend + scrapers)
- Python 3.12, type hints everywhere
- `ruff` for lint + format (line length 100, rules: E/F/I/B/DJ — see `backend/pyproject.toml`)
- Django apps go under `apps/`, never at project root
- Models declared with explicit `verbose_name` for admin clarity
- Use `gis_models.PointField(geography=True, srid=4326)` for all locations — geography type, not geometry
- Spatial queries use the ORM (`location__dwithin=...`), never raw SQL unless there's a real reason
- Tasks live in `apps/<app>/tasks.py` and are decorated with `@shared_task`

### Svelte / TypeScript
- Svelte 5 runes syntax (`$state`, `$derived`, `$effect`) — not the legacy `$:` syntax
- All API types come from `src/lib/api/schema.d.ts` (generated, never hand-edited)
- URL is the source of truth for filter state — use SvelteKit's `$page.url` and load functions
- Tailwind for styling; no component libraries unless absolutely needed

### Scrapy
- One spider per source, named after the domain (`aruodas`, `domoplius`, ...)
- Spiders only extract; pipelines validate, geocode, and persist
- `DOWNLOAD_DELAY` minimum 3s, `CONCURRENT_REQUESTS_PER_DOMAIN: 1` — be polite
- HTTPCACHE enabled in dev for fast iteration without re-hitting sites
- Use `scrapy-playwright` only when the site requires JS rendering — default to plain Scrapy

## Conventions Claude Code should follow

1. **Run tests / migrations before declaring a task done.** `docker compose exec backend python manage.py migrate --check` should pass.
2. **Don't commit secrets.** `.env` is gitignored; only `.env.example` is committed.
3. **Don't pin to `latest` Docker tags.** Use specific versions (already set in `docker-compose.yml`).
4. **Geographic data is in EPSG:4326** (lat/lng). If you ever see EPSG:3857, that's web mercator — only for tile rendering, never for storage.
5. **Listing photos: link, don't re-host.** Store the source URL of each image; the frontend can hot-link via a thumbnail proxy if needed. Avoids copyright issues.
6. **Phone numbers / contact info: don't store.** Link to original listing for contact.
7. **Migrations are sacred.** Never edit a migration that's been applied. Create a new one.

## Known gotchas

- **Nominatim first boot is slow.** It downloads + indexes the Lithuania OSM extract, ~5–10 min. Watch logs with `docker compose logs -f nominatim`. Subsequent boots are instant.
- **GeoDjango on Apple Silicon / Linux ARM** can have GDAL version mismatches. The Dockerfile pins a working combo; don't change `python:3.12-slim` casually.
- **Scrapy from inside Docker network**: it talks to `nominatim:8080`, not `localhost:7070`. Use the `NOMINATIM_URL` env var.
- **The scrapers container shares the backend volume** (`./backend:/backend`) so it can import Django models for the write pipeline. Don't break this.
- **Scrapers container uses `profiles: ["manual"]`** — it doesn't start with `docker compose up`. You must explicitly run it with `docker compose run --rm scrapers ...`.
- **Django settings split**: `config/settings/base.py`, `dev.py`, `prod.py`. Use `DJANGO_SETTINGS_MODULE=config.settings.dev` in `.env`.

## Don't do

- Don't add Node dependencies for things that have Python equivalents (and vice versa).
- Don't create Django apps for tiny features — extend existing ones first.
- Don't bypass the Scrapy item pipeline by writing directly to the DB from a spider.
- Don't hardcode 5000 (5 km) — read from `settings.DEFAULT_SEARCH_RADIUS_M`.
- Don't add authentication until Phase 4 (see BUILD_PLAN.md).
- Don't scrape Aruodas without `scrapy-playwright` + `curl_cffi`. Plain Scrapy will get you blocked instantly.
- Don't editorialize about developers in the UI. Stick to facts (permit counts, dates, sizes). Don't claim quality / reputation unless sourced.
- Don't republish gov PDFs. Link to source. Cache locally for our extraction pipeline only.
- Don't pay for Registrų centras NTR API in MVP. Use only free GeoPortal cadastre + Infostatyba.

## Government data — guiding principles

Phase 7+ adds Lithuanian government data sources. Read `GOV_DATA_SOURCES.md` first.

- **GeoPortal WFS layers** are real OGC services. Use a proper WFS client (e.g., `OWSLib`), not bespoke scraping.
- **JAR open data dump** is downloaded as a CSV/JSON file and bulk-imported. Refresh monthly. Filter to NACE codes 41.10, 41.20, 68.10, 68.20, 68.31.
- **Infostatyba and TPDRIS need scraping** (no official API). Same Scrapy patterns as listing sites, same throttling rules. Cite sources in UI.
- **PDF extraction**: territorial plans are mostly PDFs. Use `pymupdf` for text, `tesseract` (via `pytesseract`) for scanned ones. Then run an LLM extraction pass to pull structured facts (max height, allowed uses, density). Store extracted data with `extraction_confidence`.
- **Cite everything in the UI**: "Source: TPDRIS, last synced 2024-12-01". Trust matters when surfacing gov data.
- **Polish and incomplete data is normal**. Some plans have no shapefile, only a PDF map. Flag missing data rather than guessing.

## Build process — The Loom

Development follows a TDD loop called "the loom": RED → GREEN → REFACTOR → REVIEW → IMPROVE → REPEAT.

### Custom commands

| Command | Purpose |
|---------|---------|
| `/project:build` | Main build loop — finds next task, runs TDD loom, self-improves |
| `/project:build Task 0.2` | Build a specific task |
| `/project:tdd <description>` | Single RED→GREEN→REFACTOR cycle for one behavior |
| `/project:review-improve` | Review recent work + improve the build process itself |
| `/project:next` | Show the next task from BUILD_PLAN.md |
| `/loop /project:build` | Continuous mode — keeps building tasks until phase is done |

### How the loom works

1. **Orient** — read BUILD_PLAN.md, check services, break task into testable units
2. **RED** — write one failing test (must fail, not error)
3. **GREEN** — write minimum code to pass (nothing extra)
4. **REFACTOR** — clean up while all tests stay green
5. **REVIEW** — check against CLAUDE.md conventions
6. **IMPROVE** — update skills/hooks/settings if the process had friction
7. **Repeat** — next unit, next task

### Self-improvement

The build process updates itself. After each task, the loom checks:
- Were there permission prompts for safe commands? → updates `.claude/settings.json`
- Was a convention missing? → proposes CLAUDE.md addition
- Was a build step unhelpful? → edits `.claude/commands/build.md`
- Was there a non-obvious learning? → saves to memory

### Hooks (automatic)

| Trigger | Hook | What it does |
|---------|------|-------------|
| After editing `.py` file | `check-syntax.sh` | Fast syntax check (no Docker, catches errors before test run) |
| After running `pytest` | `post-test-report.sh` | Reminds which loom phase you're in if tests fail |

### Decision policy

The build loop runs autonomously. It only asks the user about:
- Data model design (fields, relationships)
- API contracts (endpoints, response shapes)
- Ambiguous BUILD_PLAN.md requirements
- Trade-offs between valid approaches
- Anything that crosses phase boundaries

## When in doubt

Read `BUILD_PLAN.md` for the current phase's scope. Don't pull work from a later phase into the current one.
