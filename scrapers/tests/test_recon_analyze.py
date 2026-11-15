"""Tests for the pure analysis half of the site inspector (no browser needed).

    docker compose exec scraper_worker python -m unittest discover -s tests -v
"""

import unittest

from recon.analyze import (
    card_patterns,
    is_read_only,
    pagination_links,
    render_report,
    safe_url,
)


def link(href: str, text: str = "", container: str = "div.item") -> dict:
    return {"kind": "link", "href": href, "text": text, "container": container}


class ReadOnlyGuardTest(unittest.TestCase):
    def test_only_safe_methods_pass(self) -> None:
        for method in ("GET", "HEAD", "OPTIONS", "get"):
            self.assertTrue(is_read_only(method))
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            self.assertFalse(is_read_only(method))


class SafeUrlTest(unittest.TestCase):
    def test_keeps_path_and_query_keys_drops_values(self) -> None:
        self.assertEqual(
            safe_url("https://api.site.lt/search?q=kretinga&page=2&token=abc"),
            "https://api.site.lt/search?q&page&token",
        )

    def test_no_query(self) -> None:
        self.assertEqual(safe_url("https://site.lt/a/b"), "https://site.lt/a/b")


class CardPatternsTest(unittest.TestCase):
    def test_repeated_containers_rank_by_count(self) -> None:
        links = [link(f"/ad/{i}", f"House {i}", "div.list-row") for i in range(8)]
        links += [link(f"/menu/{i}", "Menu", "li.nav") for i in range(4)]
        links += [link("/about", "About", "footer.site")]
        patterns = card_patterns(links)
        self.assertEqual(patterns[0]["container"], "div.list-row")
        self.assertEqual(patterns[0]["count"], 8)
        self.assertEqual(patterns[0]["sample_hrefs"][:2], ["/ad/0", "/ad/1"])
        self.assertNotIn("footer.site", [p["container"] for p in patterns])

    def test_minimum_repeat(self) -> None:
        links = [link(f"/x/{i}", container="div.pair") for i in range(3)]
        self.assertEqual(card_patterns(links, min_count=4), [])


class PaginationTest(unittest.TestCase):
    def test_detects_numbers_next_words_and_rel_next(self) -> None:
        links = [
            link("/p/2", "2"),
            link("/p/3", "3"),
            link("/p/next", "Kitas »"),
            {**link("/p/n", "→"), "rel": "next"},
            link("/ad/55", "Namas Kretingoje"),
        ]
        found = [p["href"] for p in pagination_links(links)]
        self.assertEqual(found, ["/p/2", "/p/3", "/p/next", "/p/n"])


class PaginationByHrefTest(unittest.TestCase):
    def test_detects_page_params_in_href(self) -> None:
        links = [
            link("/skelbimai/namai?action_type=1&page_nr=2", "Toliau"),
            link("/namai/puslapis/3/", ""),
            link("/list?page=4", "icon"),
            link("/skelbimai/namas-8781433.html", "Namas"),
        ]
        found = [p["href"] for p in pagination_links(links)]
        self.assertEqual(found, [
            "/skelbimai/namai?action_type=1&page_nr=2", "/namai/puslapis/3/", "/list?page=4",
        ])


class RenderGroupingTest(unittest.TestCase):
    def _page(self, elements: list[dict], network: list[dict] | None = None) -> dict:
        return {"url": "https://domoplius.lt/x", "title": "t", "status": 200,
                "elements": elements, "forms": [], "network": network or [], "blocked": []}

    def test_repeated_buttons_shown_once_with_count(self) -> None:
        save = {"kind": "button", "tag": "button", "text": "Įsiminti", "id": "", "classes": [],
                "name": "", "selector": "button.save", "matches": 20}
        report = render_report("d", [self._page([save] * 20)])
        self.assertEqual(report.count("Įsiminti"), 1)
        self.assertIn("×20", report)

    def test_first_party_endpoints_listed_before_third_party(self) -> None:
        net = [
            {"method": "GET", "status": 200, "url": "https://ads.doubleclick.net/x", "json_keys": ""},
            {"method": "GET", "status": 200, "url": "https://ads.doubleclick.net/x", "json_keys": ""},
            {"method": "GET", "status": 200, "url": "https://domoplius.lt/api/list", "json_keys": "items"},
        ]
        report = render_report("d", [self._page([], net)])
        self.assertLess(report.index("domoplius.lt/api/list"), report.index("doubleclick"))
        self.assertIn("third-party", report)


class FollowTargetsTest(unittest.TestCase):
    def _page(self) -> dict:
        cards = [link(f"/ad/{i}", f"Ad {i}", "article.card") for i in range(6)]
        return {"elements": cards + [link("https://other.lt/x", "ext", "article.card")]}

    def test_zero_means_no_follow(self) -> None:
        from recon.inspect_site import follow_targets

        self.assertEqual(follow_targets(self._page(), "https://site.lt/list", 0), [])

    def test_follows_same_site_card_links_up_to_limit(self) -> None:
        from recon.inspect_site import follow_targets

        self.assertEqual(
            follow_targets(self._page(), "https://site.lt/list", 2),
            ["https://site.lt/ad/0", "https://site.lt/ad/1"],
        )


class RenderReportTest(unittest.TestCase):
    def test_report_lists_buttons_with_selectors(self) -> None:
        page = {
            "url": "https://domoplius.lt/x",
            "title": "Namai",
            "status": 200,
            "elements": [
                {
                    "kind": "button",
                    "tag": "button",
                    "text": "Ieškoti",
                    "id": "search-btn",
                    "classes": ["btn", "btn-primary"],
                    "name": "",
                    "selector": "#search-btn",
                    "matches": 1,
                }
            ],
            "forms": [],
            "network": [],
            "blocked": [],
            "screenshot": "page-01.png",
        }
        report = render_report("domoplius", [page])
        self.assertIn("# Site inspection: domoplius", report)
        self.assertIn("Ieškoti", report)
        self.assertIn("`#search-btn`", report)
        self.assertIn("btn btn-primary", report)


if __name__ == "__main__":
    unittest.main()
