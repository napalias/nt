"""Source registry: every site the app scans, in one typed list.

This list drives three things:
- the celery-beat schedule (`config/celery.py` makes one entry per enabled source),
- `run_spider` (`apps/listings/tasks.py` builds the `scrapy crawl` command from it),
- the /scans status view (`/api/scrapes/summary` lists every source, even never-run ones).

Plain Python on purpose: no Django or Celery imports, so the scrapers containers (which
mount the backend at /backend) can import it too. A spider finds its own config with
`get_source(self.name)`; `run_spider` passes the target's property type as `-a target=...`.

Scope (principal, 2026-11-15): Kretinga district plus the HOUSE_CRITERIA.md villages,
houses for sale only. Each source lists one `Target` per property type it can scan; the
`SCAN_PROPERTY_TYPES` setting picks which run, so flats or plots are switched on by adding
a target here and the type to that setting. Every listing target is for-sale ads.

Rekvizitai (a company directory behind Cloudflare) and Facebook (login required, scraping
forbidden) are deliberately not sources.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

# CLAUDE.md politeness floor: DOWNLOAD_DELAY is never below 3 s.
MIN_DELAY_S = 3.0
DEFAULT_TIMEOUT_S = 3600

# Mirrors Listing.PropertyType values (a test keeps them in step); kept here so this
# module needs no Django import.
PROPERTY_TYPES = ("house", "flat", "plot", "commercial", "cottage", "part_of_house")

RUN_SPIDER_TASK = "apps.listings.tasks.run_spider"

# ScrapeRun.spider and Listing.source are CharField(max_length=32).
_SPIDER_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_ARG_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Kind(StrEnum):
    LISTING = "listing"  # items land in Listing with source == spider name
    GOV = "gov"  # permits, planning documents


class FetchMode(StrEnum):
    PLAIN = "plain"  # plain Scrapy HTTP
    CURL_CFFI = "curl_cffi"  # browser TLS fingerprint, no JS
    PLAYWRIGHT = "playwright"  # real browser, JS rendered


class State(StrEnum):
    SCHEDULED = "scheduled"  # on the beat schedule
    PAUSED = "paused"  # spider exists but is off the schedule; `note` says why
    NOT_BUILT = "not_built"  # registered, spider not written yet
    NEEDS_LOGIN = "needs_login"  # no spider on purpose: the data is behind a login; see `note`


@dataclass(frozen=True)
class ScanArea:
    """Where scans look. `slug` is the `region` arg the listing spiders take."""

    slug: str
    municipality: str
    places: tuple[str, ...]


SCAN_AREA = ScanArea(
    slug="kretinga",
    municipality="Kretingos r. sav.",
    # HOUSE_CRITERIA.md § Location; all inside Kretinga district.
    places=("Kretinga", "Dupulčiai", "Klonaičiai", "Jakubavai", "Raguviškiai", "Kartena"),
)


@dataclass(frozen=True)
class Schedule:
    """When beat starts a source's scan (Europe/Vilnius, crontab fields)."""

    hour: int
    minute: int = 0
    day_of_week: str = "*"

    def __post_init__(self) -> None:
        if not 0 <= self.hour <= 23:
            raise ValueError(f"Schedule hour must be 0-23, got {self.hour}")
        if not 0 <= self.minute <= 59:
            raise ValueError(f"Schedule minute must be 0-59, got {self.minute}")
        if not self.day_of_week.strip():
            raise ValueError("Schedule day_of_week must not be empty")

    def crontab_kwargs(self) -> dict[str, int | str]:
        return {"minute": self.minute, "hour": self.hour, "day_of_week": self.day_of_week}

    def __str__(self) -> str:
        return f"{self.minute} {self.hour} * * {self.day_of_week}"


def _frozen_args(args: Mapping[str, str], owner: str) -> Mapping[str, str]:
    for name, value in args.items():
        if not _ARG_NAME.match(name):
            raise ValueError(f"{owner}: invalid spider argument name {name!r}")
        if not isinstance(value, str):
            raise ValueError(f"{owner}: spider argument {name!r} must be a str, got {value!r}")
    return MappingProxyType(dict(args))


@dataclass(frozen=True)
class Target:
    """One kind of ad a source can scan, and where its index pages start.

    `property_type` is a Listing.PropertyType value; gov sources use "" (one target,
    always scanned). `spider_args` are added to the source's own args for this target.
    """

    property_type: str
    start_urls: tuple[str, ...]
    spider_args: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        owner = f"target {self.property_type or '(gov)'}"
        if self.property_type and self.property_type not in PROPERTY_TYPES:
            raise ValueError(f"{owner}: unknown property type {self.property_type!r}")
        if not self.start_urls:
            raise ValueError(f"{owner}: needs at least one start URL")
        for url in self.start_urls:
            if not url.startswith("https://"):
                raise ValueError(f"{owner}: start URL must be https, got {url!r}")
        object.__setattr__(self, "spider_args", _frozen_args(self.spider_args, owner))


@dataclass(frozen=True)
class Source:
    """One site we scan. `spider` is also ScrapeRun.spider and, for listings, Listing.source."""

    spider: str
    label: str
    kind: Kind
    fetch_mode: FetchMode
    delay_s: float  # forwarded as -s DOWNLOAD_DELAY, so it beats the spider's own setting
    schedule: Schedule
    targets: tuple[Target, ...]
    enabled: bool = True  # on the beat schedule
    built: bool = True  # a spider with this name exists in scrapers/
    needs_login: bool = False  # no spider, deliberately: listing data is behind a login
    spider_args: Mapping[str, str] = field(default_factory=dict)  # forwarded as -a
    timeout_s: int = DEFAULT_TIMEOUT_S
    note: str = ""

    def __post_init__(self) -> None:
        if not _SPIDER_NAME.match(self.spider):
            raise ValueError(
                f"Invalid spider name {self.spider!r}: lowercase letters, digits, '-', ≤32"
            )
        owner = f"source {self.spider}"
        if not self.label.strip():
            raise ValueError(f"{owner}: label must not be blank")
        if self.delay_s < MIN_DELAY_S:
            raise ValueError(f"{owner}: delay {self.delay_s}s is below the {MIN_DELAY_S}s floor")
        if self.timeout_s <= 0:
            raise ValueError(f"{owner}: timeout must be positive, got {self.timeout_s}")
        if self.enabled and not self.built:
            raise ValueError(f"{owner}: cannot be enabled while its spider is not built")
        if self.needs_login and (self.built or self.enabled):
            raise ValueError(f"{owner}: a needs-login source has no spider and is never enabled")
        if not self.targets:
            raise ValueError(f"{owner}: needs at least one target")
        types = [t.property_type for t in self.targets]
        if len(types) != len(set(types)):
            raise ValueError(f"{owner}: duplicate target property types {types}")
        if self.kind == Kind.GOV and types != [""]:
            raise ValueError(f"{owner}: gov sources have exactly one untyped target")
        if self.kind == Kind.LISTING and "" in types:
            raise ValueError(f"{owner}: listing targets need a property type")
        object.__setattr__(self, "spider_args", _frozen_args(self.spider_args, owner))

    @property
    def start_urls(self) -> tuple[str, ...]:
        return tuple(url for t in self.targets for url in t.start_urls)

    @property
    def state(self) -> State:
        if self.needs_login:
            return State.NEEDS_LOGIN
        if not self.built:
            return State.NOT_BUILT
        return State.SCHEDULED if self.enabled else State.PAUSED

    def target(self, property_type: str | None) -> Target:
        """The target for `property_type`; None means the only one (error if several)."""
        if property_type is None:
            if len(self.targets) == 1:
                return self.targets[0]
            raise ValueError(f"{self.spider} has several targets; name a property type")
        for target in self.targets:
            if target.property_type == property_type:
                return target
        known = [t.property_type for t in self.targets]
        raise ValueError(f"{self.spider} has no {property_type!r} target (has {known})")

    def active_targets(self, property_types: Iterable[str]) -> tuple[Target, ...]:
        """Targets to scan when `property_types` are switched on. Gov targets always run."""
        wanted = set(property_types)
        unknown = wanted - set(PROPERTY_TYPES)
        if unknown:
            raise ValueError(f"Unknown property types {sorted(unknown)}; use {PROPERTY_TYPES}")
        return tuple(t for t in self.targets if not t.property_type or t.property_type in wanted)

    def crawl_args(self, target: Target, overrides: Mapping[str, object]) -> dict[str, str]:
        """Spider args for one crawl: source args, then target args, then call overrides."""
        args = {**self.spider_args, **target.spider_args}
        if target.property_type:
            args["target"] = target.property_type
        for name, value in overrides.items():
            if not _ARG_NAME.match(name):
                raise ValueError(f"{self.spider}: invalid spider argument name {name!r}")
            args[name] = str(value)
        return args


def crawl_command(source: Source, target: Target, overrides: Mapping[str, object]) -> list[str]:
    """`scrapy crawl` argv for one target of a source (no shell involved)."""
    cmd = ["scrapy", "crawl", source.spider, "-s", f"DOWNLOAD_DELAY={source.delay_s:g}"]
    for name, value in source.crawl_args(target, overrides).items():
        cmd.extend(["-a", f"{name}={value}"])
    return cmd


def beat_entry_name(spider: str) -> str:
    return f"scrape-{spider}"


# --- The registry -------------------------------------------------------------------
# Start URLs, fetch modes and delays: issue #6 starting table (Fable probes, 2026-11-15).
# "unverified" in a note means #6 (recon v2) still has to confirm the Kretinga URL.
# Times are staggered: the scraper worker runs one crawl at a time, and classification
# starts at 09:00.

_REGION = {"region": SCAN_AREA.slug}


def _houses(*urls: str, **args: str) -> tuple[Target, ...]:
    return (Target("house", urls, args),)


SOURCES: tuple[Source, ...] = (
    Source(
        spider="aruodas",
        label="Aruodas.lt",
        kind=Kind.LISTING,
        fetch_mode=FetchMode.PLAYWRIGHT,
        delay_s=5,
        schedule=Schedule(hour=7, minute=0),
        targets=_houses("https://www.aruodas.lt/namai/kretingoje/", property_type="namai"),
        spider_args={**_REGION, "max_pages": "3"},
        enabled=False,
        note=(
            "Blocked: PerimeterX 403 and robots.txt disallows all. Runs only as a "
            "time-boxed human-assisted session, at most 3 pages a day (#15)."
        ),
    ),
    Source(
        spider="realu",
        label="Realu.lt",
        kind=Kind.LISTING,
        fetch_mode=FetchMode.PLAIN,
        delay_s=3,
        schedule=Schedule(hour=7, minute=10),
        targets=_houses(
            "https://www.realu.lt/nekilnojamasis-turtas/pirkti/namas/kretingos-r-sav",
        ),
        spider_args={**_REGION, "max_pages": "10"},
        note=(
            "Plain HTTP, no browser: reads the search form on the start URL, then POSTs the "
            "site's own getEstates endpoint (JSON, 31 houses a page; 110 houses, 4 pages on "
            "2026-11-16)."
        ),
    ),
    Source(
        spider="domoplius",
        label="Domoplius.lt",
        kind=Kind.LISTING,
        fetch_mode=FetchMode.PLAIN,
        delay_s=3,
        schedule=Schedule(hour=6, minute=0),
        targets=_houses(
            # Page 1 is the bare URL (`?page=1` redirects to it); the spider adds `?page=N`.
            "https://domoplius.lt/skelbimai/namai-kotedzai-sodai/kretinga",
            property_type="namai",
        ),
        spider_args={**_REGION, "max_pages": "10"},
        note="Plain HTTP, no browser: reads the JSON embedded in div#app[data-page].",
    ),
    Source(
        spider="alio",
        label="Alio.lt",
        kind=Kind.LISTING,
        fetch_mode=FetchMode.CURL_CFFI,
        delay_s=3,
        schedule=Schedule(hour=6, minute=20),
        targets=_houses(
            # Category 1433 = houses, farmsteads, garden houses for sale in the district.
            # Page 1 is this URL; the spider adds the page as `/N/` before the query.
            "https://www.alio.lt/paieska/kretingos-r-sav/?category_id=1433",
        ),
        spider_args={**_REGION, "max_pages": "5"},
        note=(
            "curl_cffi (Chrome TLS fingerprint), no browser: Scrapy's own HTTP client gets a "
            "403 here although recon's `requests` fetch got the page."
        ),
    ),
    Source(
        spider="skelbiu",
        label="Skelbiu.lt",
        kind=Kind.LISTING,
        fetch_mode=FetchMode.CURL_CFFI,
        delay_s=3,
        schedule=Schedule(hour=6, minute=40),
        targets=_houses(
            # A keyword search, not a category: the spider keeps only Kretinga district ads
            # under the Namai category, judged on each listing page. Pages are `/N`.
            "https://www.skelbiu.lt/paieska/namai-kretingoje/",
        ),
        spider_args={**_REGION, "max_pages": "10"},
        note=(
            "curl_cffi (Chrome TLS fingerprint): listing pages answer plain HTTP with a 403. "
            "Most search hits are not Kretinga houses, so each listing page is checked."
        ),
    ),
    Source(
        spider="rasknt",
        label="RaskNT.lt",
        kind=Kind.LISTING,
        fetch_mode=FetchMode.PLAIN,
        delay_s=3,
        schedule=Schedule(hour=7, minute=30),
        targets=_houses("https://rasknt.lt/namai/kretinga/"),
        spider_args={**_REGION, "max_pages": "10"},
        enabled=False,
        built=False,
        needs_login=True,
        note=(
            "Needs login, no spider (#13, checked 2026-11-16). The public index cards link only "
            "to system.rasknt.lt (login), never to the original ad, so no item could carry a "
            "source_url; RaskNT also re-lists other portals' ads. Declares ai-train=no. No "
            "credentials are stored."
        ),
    ),
    Source(
        spider="capital",
        label="Capital.lt",
        kind=Kind.LISTING,
        fetch_mode=FetchMode.PLAIN,
        delay_s=3,
        schedule=Schedule(hour=7, minute=20),
        targets=_houses(
            # Page 1 is the bare URL; all 12 results fit on it ("rezultatai (12)", no pager).
            "https://www.capital.lt/lt/nekilnojamas-turtas/pirkti/kretingos-r-sav/namai",
        ),
        spider_args={**_REGION, "max_pages": "3"},
        note=(
            "Plain HTTP. Declares ai-train=no: the text is input to judging, never training. "
            "No pager seen at 12 results (?page=2 is empty), so the walk stops after page 1."
        ),
    ),
    Source(
        spider="meguvosbustas",
        label="Meguvos būstas",
        kind=Kind.LISTING,
        fetch_mode=FetchMode.PLAIN,
        delay_s=3,
        schedule=Schedule(hour=7, minute=40),
        targets=_houses("https://meguvosbustas.lt/paieska"),
        spider_args={**_REGION, "max_pages": "10"},
        enabled=False,
        built=False,
        note="Spider not built yet (#14).",
    ),
    Source(
        spider="pamario-nt",
        label="Pamario NT",
        kind=Kind.LISTING,
        fetch_mode=FetchMode.PLAIN,
        delay_s=10,
        schedule=Schedule(hour=7, minute=50),
        targets=_houses("https://www.pamario-nt.lt/lt/kretinga/namai---"),
        spider_args={**_REGION, "max_pages": "10"},
        enabled=False,
        built=False,
        note="Spider not built yet (#14). robots.txt asks for a 10 s crawl delay.",
    ),
    Source(
        spider="ntseptynetas",
        label="NT Septynetas",
        kind=Kind.LISTING,
        fetch_mode=FetchMode.PLAIN,
        delay_s=10,
        schedule=Schedule(hour=8, minute=0),
        targets=_houses("https://ntseptynetas.lt/"),
        spider_args={**_REGION, "max_pages": "10"},
        enabled=False,
        built=False,
        note=(
            "Spider not built yet (#14). WordPress + WPL; listing page unverified (#6). "
            "10 s crawl delay."
        ),
    ),
    Source(
        spider="alginosnt",
        label="Alginos NT",
        kind=Kind.LISTING,
        fetch_mode=FetchMode.PLAIN,
        delay_s=3,
        schedule=Schedule(hour=8, minute=10),
        targets=_houses("https://alginosnt.lt/nekilnojamas-turtas/namas"),
        spider_args={**_REGION, "max_pages": "10"},
        enabled=False,
        built=False,
        note="Spider not built yet (#14).",
    ),
    Source(
        spider="kretingosskelbimai",
        label="Kretingos skelbimai",
        kind=Kind.LISTING,
        fetch_mode=FetchMode.PLAIN,
        delay_s=3,
        schedule=Schedule(hour=8, minute=20),
        targets=_houses(
            *(
                f"https://www.kretingosskelbimai.lt/skelbimai/nekilnojamas-turtas/{n}.html"
                for n in range(1, 10)
            )
        ),
        spider_args={**_REGION, "max_pages": "9"},
        enabled=False,
        built=False,
        note="Spider not built yet (#14). Mixed ad types: keep houses for sale only.",
    ),
    Source(
        spider="infostatyba",
        label="Infostatyba",
        kind=Kind.GOV,
        fetch_mode=FetchMode.PLAYWRIGHT,
        delay_s=5,
        schedule=Schedule(hour=5, minute=0),
        targets=(Target("", ("https://www.planuojustatyti.lt/statybos-leidimu-paieska",)),),
        spider_args={"max_pages": "20"},
        timeout_s=7200,
        note="Building permits (planuojustatyti.lt). National search, not Kretinga-only yet.",
    ),
    Source(
        spider="tpdris",
        label="TPDRIS",
        kind=Kind.GOV,
        fetch_mode=FetchMode.PLAYWRIGHT,
        delay_s=5,
        schedule=Schedule(hour=4, minute=0, day_of_week="sun"),
        targets=(Target("", ("https://www.tpdris.lt/lt/paieska",)),),
        spider_args={"max_pages": "50"},
        timeout_s=7200,
        note="Territorial planning documents. National search, not Kretinga-only yet.",
    ),
)

_BY_SPIDER: Mapping[str, Source] = MappingProxyType({s.spider: s for s in SOURCES})
if len(_BY_SPIDER) != len(SOURCES):
    raise ValueError("Source registry has duplicate spider names")


def get_source(spider: str) -> Source:
    try:
        return _BY_SPIDER[spider]
    except KeyError:
        raise ValueError(f"Unknown source {spider!r}; registered: {sorted(_BY_SPIDER)}") from None


def scheduled_sources() -> tuple[Source, ...]:
    """Sources that get a beat entry."""
    return tuple(s for s in SOURCES if s.enabled)
