# Scrapy settings for realestate_spiders project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html

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

# HTTP cache — enabled for dev to avoid re-hitting sites during iteration
HTTPCACHE_ENABLED = True
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
# Item pipelines — added as they are implemented
# ---------------------------------------------------------------------------
ITEM_PIPELINES: dict[str, int] = {
    # Listing pipelines (skip non-ListingItem automatically)
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
