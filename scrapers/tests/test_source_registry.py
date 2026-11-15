"""The backend source registry and the Scrapy project must agree on which spiders exist.

The registry is plain Python in backend/apps/listings/sources.py; the scraper containers
mount the backend at /backend (on PYTHONPATH), so spiders import it directly.

    docker compose exec scraper_worker python -m unittest discover -s tests
"""

import subprocess
import sys
import unittest

from apps.listings.sources import SOURCES, get_source
from scrapy.spiderloader import SpiderLoader
from scrapy.utils.project import get_project_settings

# Spiders that are not data sources.
NOT_SOURCES = {"test"}


class SourceRegistryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.project_spiders = set(SpiderLoader.from_settings(get_project_settings()).list())

    def test_every_built_source_has_a_spider(self) -> None:
        built = {s.spider for s in SOURCES if s.built}
        self.assertEqual(built - self.project_spiders, set())

    def test_unbuilt_sources_have_no_spider_yet(self) -> None:
        # Flip `built=True` in the registry in the same change that adds the spider.
        unbuilt = {s.spider for s in SOURCES if not s.built}
        self.assertEqual(unbuilt & self.project_spiders, set())

    def test_every_spider_is_registered(self) -> None:
        registered = {s.spider for s in SOURCES}
        self.assertEqual(self.project_spiders - NOT_SOURCES - registered, set())

    def test_built_spiders_accept_the_args_run_spider_sends(self) -> None:
        loader = SpiderLoader.from_settings(get_project_settings())
        for source in (s for s in SOURCES if s.built):
            for target in source.targets:
                with self.subTest(spider=source.spider, target=target.property_type):
                    args = source.crawl_args(target, {})
                    spider = loader.load(source.spider)(**args)
                    for name, value in args.items():
                        self.assertEqual(str(getattr(spider, name)), value)

    def test_registry_delay_beats_spider_custom_settings(self) -> None:
        # run_spider passes `-s DOWNLOAD_DELAY=<registry delay>` (cmdline priority).
        settings = get_project_settings()
        settings.set("DOWNLOAD_DELAY", 10, priority="cmdline")
        spidercls = SpiderLoader.from_settings(settings).load("aruodas")  # custom 5 s
        spidercls.update_settings(settings)
        self.assertEqual(settings.getfloat("DOWNLOAD_DELAY"), 10)

    def test_registry_imports_without_django(self) -> None:
        # Spiders may read it before (or without) Django setup, so it must stay pure Python.
        probe = "import sys, apps.listings.sources; sys.exit('django' in sys.modules)"
        result = subprocess.run([sys.executable, "-c", probe], capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertTrue(get_source("domoplius").start_urls)


if __name__ == "__main__":
    unittest.main()
