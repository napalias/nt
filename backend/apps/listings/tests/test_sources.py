"""The source registry: one typed list of every site we scan (pure Python, no DB)."""

import pytest

from apps.listings.models import Listing
from apps.listings.sources import (
    MIN_DELAY_S,
    PROPERTY_TYPES,
    SCAN_AREA,
    SOURCES,
    FetchMode,
    Kind,
    Schedule,
    Source,
    State,
    Target,
    crawl_command,
    get_source,
    scheduled_sources,
)

LISTING_SPIDERS = {
    "aruodas",
    "realu",
    "domoplius",
    "alio",
    "skelbiu",
    "rasknt",
    "capital",
    "meguvosbustas",
    "pamario-nt",
    "ntseptynetas",
    "alginosnt",
    "kretingosskelbimai",
}
GOV_SPIDERS = {"infostatyba", "tpdris"}
NOT_BUILT = {
    "realu",
    "capital",
    "rasknt",
    "ntseptynetas",  # no listings on the site (#14): a note in the registry, no spider
}

# Issue #6 starting table: fetch mode per site; pamario-nt and ntseptynetas honour a
# 10 s robots Crawl-delay.
FETCH_MODES = {
    "aruodas": FetchMode.PLAYWRIGHT,
    "realu": FetchMode.PLAIN,
    "domoplius": FetchMode.PLAIN,
    "alio": FetchMode.PLAYWRIGHT,
    "skelbiu": FetchMode.PLAYWRIGHT,
    "rasknt": FetchMode.PLAIN,
    "capital": FetchMode.PLAIN,
    "meguvosbustas": FetchMode.PLAIN,
    "pamario-nt": FetchMode.PLAIN,
    "ntseptynetas": FetchMode.PLAIN,
    "alginosnt": FetchMode.PLAIN,
    "kretingosskelbimai": FetchMode.PLAIN,
    "infostatyba": FetchMode.PLAYWRIGHT,
    "tpdris": FetchMode.PLAYWRIGHT,
}


def _by_kind(kind: Kind) -> set[str]:
    return {s.spider for s in SOURCES if s.kind == kind}


class TestRegistryContents:
    def test_listing_sources(self):
        assert _by_kind(Kind.LISTING) == LISTING_SPIDERS

    def test_gov_sources(self):
        assert _by_kind(Kind.GOV) == GOV_SPIDERS

    def test_spiders_are_unique(self):
        spiders = [s.spider for s in SOURCES]
        assert len(spiders) == len(set(spiders))

    @pytest.mark.parametrize("source", SOURCES, ids=lambda s: s.spider)
    def test_every_source_is_fully_configured(self, source: Source):
        assert source.label.strip()
        assert source.start_urls
        assert all(url.startswith("https://") for url in source.start_urls)
        assert isinstance(source.fetch_mode, FetchMode)
        assert source.delay_s >= MIN_DELAY_S
        assert isinstance(source.schedule, Schedule)
        assert isinstance(source.enabled, bool)
        assert all(isinstance(v, str) for v in source.spider_args.values())

    def test_fetch_modes_follow_issue_6(self):
        assert {s.spider: s.fetch_mode for s in SOURCES} == FETCH_MODES

    def test_crawl_delay_sites_wait_10_seconds(self):
        assert get_source("pamario-nt").delay_s == 10
        assert get_source("ntseptynetas").delay_s == 10

    def test_unbuilt_spiders_are_registered_but_disabled(self):
        unbuilt = {s.spider for s in SOURCES if not s.built}
        assert unbuilt == NOT_BUILT
        for spider in NOT_BUILT:
            source = get_source(spider)
            assert source.enabled is False
            assert source.state == State.NOT_BUILT

    def test_rekvizitai_and_facebook_are_not_sources(self):
        for source in SOURCES:
            text = " ".join((source.spider, source.label, *source.start_urls)).lower()
            assert "rekvizitai" not in text
            assert "facebook" not in text

    def test_property_types_mirror_listing_model(self):
        assert set(PROPERTY_TYPES) == set(Listing.PropertyType.values)


class TestKretingaHousesScope:
    def test_scan_area_is_kretinga_district_and_criteria_villages(self):
        assert SCAN_AREA.slug == "kretinga"
        assert SCAN_AREA.municipality == "Kretingos r. sav."
        assert {
            "Kretinga",
            "Dupulčiai",
            "Klonaičiai",
            "Jakubavai",
            "Raguviškiai",
            "Kartena",
        } <= set(SCAN_AREA.places)

    @pytest.mark.parametrize(
        "source", [s for s in SOURCES if s.kind == Kind.LISTING], ids=lambda s: s.spider
    )
    def test_listing_sources_scan_kretinga_houses_for_sale(self, source: Source):
        assert [t.property_type for t in source.targets] == ["house"]
        assert source.spider_args["region"] == "kretinga"

    def test_domoplius_keeps_its_site_vocabulary(self):
        args = get_source("domoplius").crawl_args(get_source("domoplius").target("house"), {})
        assert args["property_type"] == "namai"
        assert args["region"] == "kretinga"

    def test_gov_sources_have_one_untyped_target(self):
        for spider in GOV_SPIDERS:
            assert [t.property_type for t in get_source(spider).targets] == [""]

    def test_active_targets_follow_switched_on_property_types(self):
        domoplius = get_source("domoplius")
        assert [t.property_type for t in domoplius.active_targets(["house"])] == ["house"]
        assert domoplius.active_targets(["flat"]) == ()
        # Gov sources are not split by property type: always scanned.
        assert len(get_source("tpdris").active_targets(["flat"])) == 1

    def test_unknown_property_type_is_rejected(self):
        with pytest.raises(ValueError, match="bogus"):
            get_source("domoplius").active_targets(["bogus"])


class TestLocalAgencySources:
    """#14: the five small local sources."""

    @pytest.mark.parametrize(
        "spider", ["meguvosbustas", "pamario-nt", "alginosnt", "kretingosskelbimai"]
    )
    def test_agency_spiders_are_built_and_scheduled(self, spider):
        source = get_source(spider)
        assert source.built and source.enabled
        assert source.state == State.SCHEDULED
        assert source.fetch_mode == FetchMode.PLAIN

    def test_ntseptynetas_has_no_spider_because_the_site_has_no_listings(self):
        source = get_source("ntseptynetas")
        assert source.state == State.NOT_BUILT
        assert "no property listings" in source.note.lower()

    def test_alginosnt_starts_at_the_district_filter_not_the_text_search(self):
        # "search=Kretinga" also returns Kretingale (Klaipeda district); region=25 is Kretingos r.
        (url,) = get_source("alginosnt").start_urls
        assert "region=25" in url and "search=" not in url

    def test_kretingosskelbimai_starts_at_house_sub_categories(self):
        urls = get_source("kretingosskelbimai").start_urls
        assert [u.rsplit("/", 1)[1] for u in urls] == ["namai", "sodybos"]


class TestScheduling:
    def test_enabled_sources_are_the_scheduled_ones(self):
        assert {s.spider for s in scheduled_sources()} == {s.spider for s in SOURCES if s.enabled}
        assert all(s.state == State.SCHEDULED for s in scheduled_sources())

    def test_blocked_and_national_only_spiders_are_paused(self):
        # aruodas: PerimeterX + robots disallow, human-assisted only (#15).
        # skelbiu: spider has no Kretinga filter yet (#12).
        for spider in ("aruodas", "skelbiu"):
            source = get_source(spider)
            assert source.built and not source.enabled
            assert source.state == State.PAUSED
            assert source.note

    def test_tpdris_runs_weekly(self):
        assert get_source("tpdris").schedule == Schedule(hour=4, minute=0, day_of_week="sun")

    def test_schedule_renders_as_cron(self):
        assert str(Schedule(hour=6, minute=5)) == "5 6 * * *"
        assert Schedule(hour=4, day_of_week="sun").crontab_kwargs() == {
            "minute": 0,
            "hour": 4,
            "day_of_week": "sun",
        }


class TestCrawlCommand:
    def test_forwards_registry_args_target_and_delay(self):
        source = get_source("pamario-nt")
        cmd = crawl_command(source, source.target("house"), {})
        assert cmd[:3] == ["scrapy", "crawl", "pamario-nt"]
        assert cmd[3:5] == ["-s", "DOWNLOAD_DELAY=10"]
        pairs = [cmd[i + 1] for i, part in enumerate(cmd) if part == "-a"]
        assert "region=kretinga" in pairs
        assert "target=house" in pairs

    def test_overrides_win_over_registry_args(self):
        source = get_source("domoplius")
        cmd = crawl_command(source, source.target("house"), {"max_pages": 1})
        pairs = [cmd[i + 1] for i, part in enumerate(cmd) if part == "-a"]
        assert "max_pages=1" in pairs
        assert "max_pages=10" not in pairs

    def test_gov_crawl_has_no_target_arg(self):
        source = get_source("infostatyba")
        cmd = crawl_command(source, source.target(None), {})
        pairs = [cmd[i + 1] for i, part in enumerate(cmd) if part == "-a"]
        assert pairs == ["max_pages=20"]

    def test_rejects_arg_names_scrapy_cannot_take(self):
        source = get_source("domoplius")
        with pytest.raises(ValueError, match="argument name"):
            crawl_command(source, source.target("house"), {"bad name": "x"})


class TestLookups:
    def test_unknown_spider(self):
        with pytest.raises(ValueError, match="nope"):
            get_source("nope")

    def test_missing_target(self):
        with pytest.raises(ValueError, match="flat"):
            get_source("domoplius").target("flat")

    def test_single_target_is_the_default(self):
        assert get_source("domoplius").target(None).property_type == "house"


def _source(**overrides) -> Source:
    fields = {
        "spider": "example",
        "label": "Example",
        "kind": Kind.LISTING,
        "fetch_mode": FetchMode.PLAIN,
        "delay_s": 3,
        "schedule": Schedule(hour=6),
        "targets": (Target("house", ("https://example.lt/namai",)),),
    }
    fields.update(overrides)
    return Source(**fields)


class TestSourceInvariants:
    def test_valid_source_builds(self):
        assert _source().state == State.SCHEDULED

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"delay_s": 1}, "delay"),
            ({"built": False, "enabled": True}, "not built"),
            ({"spider": "Bad Name"}, "spider name"),
            ({"spider": "x" * 33}, "spider name"),
            ({"label": " "}, "label"),
            ({"targets": ()}, "target"),
            ({"kind": Kind.GOV}, "gov"),
            ({"targets": (Target("", ("https://example.lt",)),)}, "property type"),
            (
                {
                    "targets": (
                        Target("house", ("https://example.lt/a",)),
                        Target("house", ("https://example.lt/b",)),
                    )
                },
                "duplicate",
            ),
            ({"spider_args": {"bad name": "x"}}, "argument name"),
            ({"timeout_s": 0}, "timeout"),
        ],
    )
    def test_invalid_source_is_rejected(self, overrides: dict, message: str):
        with pytest.raises(ValueError, match=message):
            _source(**overrides)

    @pytest.mark.parametrize(
        ("property_type", "urls", "message"),
        [
            ("house", ("http://example.lt",), "https"),
            ("house", (), "start URL"),
            ("villa", ("https://example.lt",), "property type"),
        ],
    )
    def test_invalid_target_is_rejected(self, property_type: str, urls: tuple, message: str):
        with pytest.raises(ValueError, match=message):
            Target(property_type, urls)

    def test_several_targets_need_an_explicit_property_type(self):
        source = _source(
            targets=(
                Target("house", ("https://example.lt/namai",)),
                Target("flat", ("https://example.lt/butai",)),
            )
        )
        with pytest.raises(ValueError, match="several targets"):
            source.target(None)
        assert source.target("flat").start_urls == ("https://example.lt/butai",)

    def test_spider_args_are_read_only(self):
        source = _source(spider_args={"max_pages": "5"})
        with pytest.raises(TypeError):
            source.spider_args["max_pages"] = "6"  # type: ignore[index]

    def test_schedule_rejects_impossible_times(self):
        with pytest.raises(ValueError):
            Schedule(hour=24)
        with pytest.raises(ValueError):
            Schedule(hour=6, minute=60)
