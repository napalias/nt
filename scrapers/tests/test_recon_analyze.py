"""Tests for the pure analysis half of the site inspector (no browser needed).

    docker compose exec scraper_worker python -m unittest discover -s tests -v
"""

import unittest

from recon.analyze import (
    card_patterns,
    decide_verdict,
    detect_wall,
    host_delay_s,
    is_forbidden_host,
    is_read_only,
    pagination_links,
    render_report,
    request_allowed,
    robots_allows,
    robots_crawl_delay,
    safe_url,
    summarize_html,
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


def listing_html(n: int = 25, extra_head: str = "", title: str = "Namai Kretingoje") -> str:
    links = "".join(f'<a href="/skelbimas/{i}">Namas {i}</a>' for i in range(n))
    return f"<html><head><title>{title}</title>{extra_head}</head><body>{links}</body></html>"


class DetectWallTest(unittest.TestCase):
    def test_real_listing_page_has_no_wall(self) -> None:
        self.assertEqual(detect_wall(200, "https://s.lt/list", "Namai", listing_html()), "none")

    def test_recaptcha_widget_on_a_normal_page_is_not_a_wall(self) -> None:
        html = listing_html(extra_head='<script src="https://www.google.com/recaptcha/api.js"></script>')
        self.assertEqual(detect_wall(200, "https://s.lt/list", "Namai", html), "none")

    def test_challenge_title_is_captcha(self) -> None:
        html = "<html><head><title>Just a moment...</title></head><body>cf-chl</body></html>"
        self.assertEqual(detect_wall(403, "https://s.lt/", "Just a moment...", html), "captcha")

    def test_perimeterx_block_page_is_captcha(self) -> None:
        html = '<html><body><div id="px-captcha"></div></body></html>'
        self.assertEqual(detect_wall(403, "https://s.lt/", "", html), "captcha")

    def test_403_mentioning_captcha_is_captcha(self) -> None:
        html = "<html><body>Please solve the captcha</body></html>"
        self.assertEqual(detect_wall(403, "https://s.lt/", "", html), "captcha")

    def test_redirect_to_login_url_is_login(self) -> None:
        html = "<html><body><form><input type=password></form></body></html>"
        self.assertEqual(detect_wall(200, "https://s.lt/prisijungti?next=/x", "", html), "login")

    def test_password_form_on_thin_page_is_login(self) -> None:
        html = "<html><body><a href='/'>Home</a><input type='password' name='p'></body></html>"
        self.assertEqual(detect_wall(200, "https://s.lt/x", "Prisijunkite", html), "login")

    def test_login_box_on_a_rich_page_is_not_a_wall(self) -> None:
        html = listing_html(40).replace("<body>", "<body><input type='password'>")
        self.assertEqual(detect_wall(200, "https://s.lt/list", "Namai", html), "none")

    def test_401_is_login(self) -> None:
        self.assertEqual(detect_wall(401, "https://s.lt/x", "", "<html></html>"), "login")

    def test_consent_wall_on_thin_page(self) -> None:
        html = "<html><body><div id='CybotCookiebotDialog'>Slapukai</div></body></html>"
        self.assertEqual(detect_wall(200, "https://s.lt/x", "", html), "cookie_wall")

    def test_cookie_banner_on_a_rich_page_is_not_a_wall(self) -> None:
        html = listing_html(40).replace("<body>", "<body><div id='CybotCookiebotDialog'></div>")
        self.assertEqual(detect_wall(200, "https://s.lt/list", "Namai", html), "none")


class SummarizeHtmlTest(unittest.TestCase):
    def test_counts_distinct_real_links_and_reads_title(self) -> None:
        html = (
            "<title>  Namai\n Kretingoje </title>"
            '<a href="/a">1</a><a href="/a">dup</a><a href="/b">2</a>'
            '<a href="#top">x</a><a href="javascript:void(0)">y</a><a>no href</a>'
        )
        summary = summarize_html("plain", 200, "https://s.lt/x", html)
        self.assertEqual(summary["title"], "Namai Kretingoje")
        self.assertEqual(summary["anchors"], 2)
        self.assertEqual(summary["method"], "plain")

    def test_hrefs_are_absolute_without_fragments(self) -> None:
        html = '<a href="/a#x">1</a><a href="b">2</a><a href="https://o.lt/c">3</a>'
        summary = summarize_html("plain", 200, "https://s.lt/dir/page", html)
        self.assertEqual(
            summary["hrefs"], {"https://s.lt/a", "https://s.lt/dir/b", "https://o.lt/c"}
        )

    def test_request_allowed_lets_only_listed_posts_through(self) -> None:
        url = "https://s.lt/index.php?route=product/category/getEstates"
        self.assertTrue(request_allowed("GET", url))
        self.assertFalse(request_allowed("POST", url))
        self.assertTrue(request_allowed("POST", url, allow_post=("getEstates",)))
        self.assertFalse(request_allowed("POST", "https://s.lt/subscribe", allow_post=("getEstates",)))
        self.assertFalse(request_allowed("DELETE", url, allow_post=("getEstates",)))

    def test_error_result_has_no_wall_and_no_anchors(self) -> None:
        summary = summarize_html("plain", None, "https://s.lt/x", "", error="timeout")
        self.assertEqual((summary["anchors"], summary["error"]), (0, "timeout"))
        self.assertEqual(summary["wall"], "none")


def result(
    method: str, status: int | None, anchors: int, wall: str = "none", error: str = ""
) -> dict:
    return {"method": method, "status": status, "anchors": anchors, "wall": wall, "error": error,
            "final_url": "https://s.lt/", "title": "", "bytes": 1000}


class DecideVerdictTest(unittest.TestCase):
    def test_plain_wins_when_it_sees_the_listing(self) -> None:
        verdict, wall, _ = decide_verdict(
            result("plain", 200, 80), result("curl_cffi", 200, 80), result("playwright", 200, 85)
        )
        self.assertEqual((verdict, wall), ("plain", "none"))

    def test_curl_cffi_when_plain_is_blocked(self) -> None:
        verdict, wall, _ = decide_verdict(
            result("plain", 403, 0, "captcha"),
            result("curl_cffi", 200, 70),
            result("playwright", 200, 75),
        )
        self.assertEqual((verdict, wall), ("curl_cffi", "none"))

    def test_playwright_when_html_is_an_empty_js_shell(self) -> None:
        verdict, _, reason = decide_verdict(
            result("plain", 200, 3), result("curl_cffi", 200, 3), result("playwright", 200, 90)
        )
        self.assertEqual(verdict, "playwright")
        self.assertIn("JS", reason)

    def test_blocked_reports_the_strongest_wall_seen(self) -> None:
        verdict, wall, _ = decide_verdict(
            result("plain", 403, 0, "captcha"),
            result("curl_cffi", 403, 0, "captcha"),
            result("playwright", 200, 2, "login"),
        )
        self.assertEqual((verdict, wall), ("blocked", "captcha"))

    def test_link_poor_plain_fetch_is_not_good_enough_next_to_a_rich_browser_page(self) -> None:
        verdict, _, _ = decide_verdict(
            result("plain", 200, 12), result("curl_cffi", 200, 12), result("playwright", 200, 120)
        )
        self.assertEqual(verdict, "playwright")

    def test_works_without_a_browser_result(self) -> None:
        verdict, _, _ = decide_verdict(result("plain", 200, 40), result("curl_cffi", 200, 40), None)
        self.assertEqual(verdict, "plain")

    def test_login_wall_with_content_still_blocked_when_nothing_usable(self) -> None:
        verdict, wall, _ = decide_verdict(
            result("plain", 200, 4, "login"), result("curl_cffi", 200, 4, "login"), None
        )
        self.assertEqual((verdict, wall), ("blocked", "login"))

    def test_all_errors_is_blocked_with_no_wall(self) -> None:
        verdict, wall, reason = decide_verdict(
            result("plain", None, 0, error="timeout"),
            result("curl_cffi", None, 0, error="timeout"),
            result("playwright", None, 0, error="timeout"),
        )
        self.assertEqual((verdict, wall), ("blocked", "none"))
        self.assertIn("timeout", reason)


def with_hrefs(fetch: dict, hrefs: list[str]) -> dict:
    return {**fetch, "hrefs": {f"https://s.lt{h}" if h.startswith("/") else h for h in hrefs},
            "anchors": len(hrefs)}


class CoverageVerdictTest(unittest.TestCase):
    NAV = [f"/menu/{i}" for i in range(150)]
    CARDS = [f"/skelbimas/{i}" for i in range(30)]

    def test_js_rendered_cards_behind_server_boilerplate_need_playwright(self) -> None:
        # 150 nav/footer links are in the server HTML; the 30 cards only after JS.
        plain = with_hrefs(result("plain", 200, 0), self.NAV)
        curl = with_hrefs(result("curl_cffi", 200, 0), self.NAV)
        browser = with_hrefs(result("playwright", 200, 0), self.NAV + self.CARDS)
        verdict, _, _ = decide_verdict(plain, curl, browser)
        self.assertEqual(verdict, "playwright")
        self.assertAlmostEqual(plain["coverage"], 150 / 180, places=2)

    def test_third_party_links_only_the_browser_loads_do_not_count(self) -> None:
        ads = [f"https://ads.example/{i}" for i in range(40)]
        plain = with_hrefs(result("plain", 200, 0), self.NAV + self.CARDS)
        browser = with_hrefs(result("playwright", 200, 0), self.NAV + self.CARDS + ads)
        verdict, _, _ = decide_verdict(plain, None, browser)
        self.assertEqual(verdict, "plain")
        self.assertEqual(plain["coverage"], 1.0)

    def test_without_href_sets_the_link_count_rule_still_applies(self) -> None:
        verdict, _, _ = decide_verdict(result("plain", 200, 80), None, result("playwright", 200, 85))
        self.assertEqual(verdict, "plain")


class PresetsTest(unittest.TestCase):
    SOURCES = {
        "aruodas", "realu", "domoplius", "alio", "skelbiu", "rasknt", "capital",
        "meguvosbustas", "pamario-nt", "ntseptynetas", "alginosnt", "kretingosskelbimai",
    }

    def test_every_nt_source_has_a_preset_on_its_own_host(self) -> None:
        from urllib.parse import urlsplit

        from recon.inspect_site import SITES

        self.assertTrue(self.SOURCES <= set(SITES), self.SOURCES - set(SITES))
        for name in self.SOURCES:
            host = urlsplit(SITES[name]).netloc.removeprefix("www.")
            self.assertTrue(host.startswith(name), (name, host))
            self.assertFalse(is_forbidden_host(host))

    def test_presets_start_in_kretinga_not_nationwide(self) -> None:
        from recon.inspect_site import SITES

        for name in ("aruodas", "realu", "domoplius", "alio", "skelbiu", "rasknt", "capital",
                     "pamario-nt", "alginosnt"):
            self.assertIn("kretin", SITES[name].lower(), name)

    def test_realu_may_post_its_search_and_nothing_else(self) -> None:
        from recon.inspect_site import PRESET_ALLOW_POST

        self.assertEqual(PRESET_ALLOW_POST, {"realu": ("getEstates",)})


class RobotsTest(unittest.TestCase):
    ROBOTS = "User-agent: *\nDisallow: /private/\nCrawl-delay: 10\n"

    def test_disallowed_path(self) -> None:
        self.assertFalse(robots_allows(self.ROBOTS, "https://s.lt/private/x"))
        self.assertTrue(robots_allows(self.ROBOTS, "https://s.lt/namai/"))

    def test_disallow_all(self) -> None:
        self.assertFalse(robots_allows("User-agent: *\nDisallow: /\n", "https://s.lt/namai/"))

    def test_empty_robots_allows(self) -> None:
        self.assertTrue(robots_allows("", "https://s.lt/namai/"))

    def test_crawl_delay(self) -> None:
        self.assertEqual(robots_crawl_delay(self.ROBOTS), 10.0)
        self.assertIsNone(robots_crawl_delay("User-agent: *\nDisallow:\n"))


class PolitenessTest(unittest.TestCase):
    def test_default_delay_is_three_seconds(self) -> None:
        self.assertEqual(host_delay_s("domoplius.lt"), 3)

    def test_slow_hosts_wait_ten_seconds_even_www_prefixed(self) -> None:
        self.assertEqual(host_delay_s("www.pamario-nt.lt"), 10)
        self.assertEqual(host_delay_s("ntseptynetas.lt"), 10)

    def test_robots_crawl_delay_raises_the_floor(self) -> None:
        self.assertEqual(host_delay_s("example.lt", robots_delay=7), 7)
        self.assertEqual(host_delay_s("example.lt", robots_delay=1), 3)

    def test_rasknt_system_host_is_never_fetched(self) -> None:
        self.assertTrue(is_forbidden_host("system.rasknt.lt"))
        self.assertFalse(is_forbidden_host("rasknt.lt"))
        self.assertTrue(is_forbidden_host("SYSTEM.rasknt.lt"))


class RenderVerdictTest(unittest.TestCase):
    def test_report_shows_the_verdict_block(self) -> None:
        page = {"url": "https://domoplius.lt/x", "title": "t", "status": 200, "elements": [],
                "forms": [], "network": [], "blocked": [],
                "fetch": {"verdict": "curl_cffi", "wall": "none", "reason": "plain got 403",
                          "results": [result("plain", 403, 0, "captcha"),
                                      result("curl_cffi", 200, 50)]}}
        report = render_report("d", [page])
        self.assertIn("Verdict: `curl_cffi`", report)
        self.assertIn("Wall: `none`", report)
        self.assertIn("captcha", report)

    def test_report_notes_a_robots_refusal(self) -> None:
        page = {"url": "https://aruodas.lt/x", "title": "", "status": None, "elements": [],
                "forms": [], "network": [], "blocked": [],
                "fetch": {"verdict": "blocked", "wall": "none", "reason": "robots.txt disallows",
                          "robots_disallowed": True, "results": []}}
        report = render_report("a", [page])
        self.assertIn("robots.txt", report)
        self.assertIn("Verdict: `blocked`", report)


if __name__ == "__main__":
    unittest.main()
