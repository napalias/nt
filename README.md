# Real Estate Aggregator

Local-first real estate aggregator for Lithuania. Scrapes multiple sources, lets you search by address with a 5 km radius.

**Read `CLAUDE.md` first** if you're using Claude Code. **Read `BUILD_PLAN.md`** for the phased build sequence.

## Quick start

```bash
# 1. Copy env
cp .env.example .env

# 2. Pull base images (one time, ~2 GB)
docker compose pull

# 3. Bring up the stack
docker compose up -d

# 4. Wait for Nominatim to finish indexing Lithuania (5–10 min on first boot)
docker compose logs -f nominatim
# Look for: "Done." then ctrl-C
```

After Phase 0 of the build plan completes, you'll have:

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000/api
- Backend admin: http://localhost:8000/admin
- OpenAPI docs: http://localhost:8000/api/docs
- Nominatim geocoder: http://localhost:7070
- MailHog UI: http://localhost:8025

## Reset everything

```bash
docker compose down -v   # nukes volumes — Nominatim will re-download on next up
```

## Run a scraper

```bash
docker compose run --rm scrapers scrapy crawl domoplius
docker compose run --rm scrapers scrapy crawl domoplius -a max_pages=2  # debug
```

## Why this stack

See `CLAUDE.md` and `BUILD_PLAN.md`. tl;dr: Scrapy + GeoDjango are best-in-class for scraping + geographic queries. SvelteKit on the frontend. Everything runs locally in Docker.
