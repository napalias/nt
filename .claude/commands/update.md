# Update database with latest listings

One-command refresh: scrape all sources for Kretinga area, then evaluate new listings.

## Steps

1. **Scrape Domoplius** (houses only for Kretinga area):
   ```bash
   docker compose run --rm scrapers scrapy crawl domoplius -a max_pages=5 -a property_type=namai -a region=kretinga -s HTTPCACHE_ENABLED=false
   ```

2. **Check validity** — mark sold/removed listings as inactive:
   ```bash
   curl -s -X POST http://localhost:8000/api/check-validity
   ```

3. **Show stats**:
   ```bash
   docker compose exec backend python -c "
   import os;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings.dev')
   import django;django.setup()
   from apps.listings.models import Listing
   active = Listing.objects.filter(is_active=True).count()
   new = Listing.objects.filter(is_active=True, evaluation__isnull=True).count()
   print(f'Active: {active}, Unclassified: {new}')
   "
   ```

4. **Evaluate new listings** — run `/evaluate` to classify unclassified ones

5. **Report** what changed: new listings found, stale removed, evaluations added
