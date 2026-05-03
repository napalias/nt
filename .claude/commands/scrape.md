# Scrape new listings

Scrape real estate listings and save them to the database.

## Steps

1. **Choose source** — ask which portal to scrape or default to Domoplius Kretinga area
2. **Fetch listings** — use WebFetch or the Bash tool to run the spider:
   ```bash
   docker compose run --rm scrapers scrapy crawl domoplius -a max_pages=2 -a region=kretinga -s HTTPCACHE_ENABLED=false
   ```
   Or for other sources:
   ```bash
   docker compose run --rm scrapers scrapy crawl alio -a max_pages=2 -a region=kretinga
   ```
3. **Report results** — show how many new listings were created/updated
4. **Classify** — run `/evaluate` to classify the new listings

## Quick scrape all sources for Kretinga

```bash
docker compose run --rm scrapers scrapy crawl domoplius -a max_pages=3 -a property_type=namai -a region=kretinga -s HTTPCACHE_ENABLED=false
docker compose run --rm scrapers scrapy crawl domoplius -a max_pages=2 -a property_type=butai -a region=kretinga -s HTTPCACHE_ENABLED=false
docker compose run --rm scrapers scrapy crawl domoplius -a max_pages=2 -a property_type=sklypai -a region=kretinga -s HTTPCACHE_ENABLED=false
```
