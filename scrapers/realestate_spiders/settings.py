# Scrapy settings for realestate_spiders project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html

import os

BOT_NAME = "realestate_spiders"

SPIDER_MODULES = ["realestate_spiders.spiders"]
NEWSPIDER_MODULE = "realestate_spiders.spiders"

# Crawl responsibly — be polite
ROBOTSTXT_OBEY = True
DOWNLOAD_DELAY = 3
CONCURRENT_REQUESTS_PER_DOMAIN = 1
CONCURRENT_REQUESTS = 4

# Logging
LOG_LEVEL = "INFO"

# HTTP cache — OFF by default: a cached index page or 403 would replay stale results into
# scheduled scans and corrupt "new items" counts. Opt in for dev iteration: SCRAPY_HTTPCACHE=1
HTTPCACHE_ENABLED = os.environ.get("SCRAPY_HTTPCACHE") == "1"
HTTPCACHE_DIR = "httpcache"
HTTPCACHE_EXPIRATION_SECS = 86400  # 24 hours

# Request fingerprinting
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"

# Async reactor for scrapy-playwright compatibility
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

# Export encoding
FEED_EXPORT_ENCODING = "utf-8"

# Realistic browser User-Agent
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# scrapy-playwright download handlers
# ---------------------------------------------------------------------------
DOWNLOAD_HANDLERS = {
    "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
}

PLAYWRIGHT_BROWSER_TYPE = "chromium"
PLAYWRIGHT_LAUNCH_OPTIONS = {"headless": True}

# ---------------------------------------------------------------------------
# Block detection: challenge/captcha/login pages (also HTTP 200 ones), 401/403/429 and
# robots-forbidden start URLs become nt/block/* stats, read into ScrapeRun by the recorder.
# 585: process_response runs from high to low order, so this sees decoded HTML (after
# HttpCompressionMiddleware, 590), a meta-refresh page before MetaRefreshMiddleware (580)
# follows it, and every 429 before RetryMiddleware (550) retries it.
# ---------------------------------------------------------------------------
DOWNLOADER_MIDDLEWARES = {
    "realestate_spiders.middlewares.BlockDetectionMiddleware": 585,
}

# ---------------------------------------------------------------------------
# Item pipelines — added as they are implemented
# ---------------------------------------------------------------------------
ITEM_PIPELINES: dict[str, int] = {
    # Listing pipelines (skip non-ListingItem automatically)
    # Scrub first: no contact data may reach validation, hashing, logs or the database.
    "realestate_spiders.pipelines.ScrubPipeline": 50,
    "realestate_spiders.pipelines.ValidatePipeline": 100,
    "realestate_spiders.pipelines.NormalizePipeline": 200,
    "realestate_spiders.pipelines.GeocodePipeline": 300,
    "realestate_spiders.pipelines.DjangoWritePipeline": 400,
    # Permit pipelines (skip non-PermitItem automatically)
    "realestate_spiders.pipelines.PermitValidatePipeline": 500,
    "realestate_spiders.pipelines.PermitMatchPlotPipeline": 600,
    "realestate_spiders.pipelines.PermitMatchDeveloperPipeline": 700,
    "realestate_spiders.pipelines.PermitWritePipeline": 800,
}

# Record each run (pages, found, new, updated, dropped) for the frontend /scans page.
EXTENSIONS = {
    "realestate_spiders.extensions.ScrapeRunRecorder": 500,
}
