"""The buyer's criteria: the one machine-readable copy.

HOUSE_CRITERIA.md (repo root) is the human-readable source. This module is what code reads:
the numeric limits, the accepted places with their coordinates, the pure filters that apply
them, and the verdict and score computed from the filter results. The prompts
(``prompts.py``) and the Claude Code command (``.claude/commands/evaluate.md``) take their
criteria text from ``describe()`` and keep no copy of their own. Change a limit in
HOUSE_CRITERIA.md and here together.

Pure Python: no Django, no network. ``python -m apps.classifier.criteria`` prints the text.

Every filter returns a ``FilterResult`` with a tri-state ``status``. A value that is missing
or cannot be trusted is ``UNKNOWN``, never ``FAIL``: only a known value outside the limits
fails, and a hard fail is the only thing that skips a listing without a closer look.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

# --- Limits (HOUSE_CRITERIA.md) ------------------------------------------------------------

PRICE_TARGET_EUR = 200_000
PRICE_MAX_EUR = 250_000
AREA_MIN_SQM = 95
AREA_PREFERRED_SQM = 100
AREA_MAX_SQM = 120
PLOT_MIN_ARES = 8
PLOT_PREFERRED_ARES = 10
PLOT_MAX_ARES = 12
# HOUSE_CRITERIA.md says "other locations between these villages and Kretinga" without a
# number. This is how far from an accepted centre, or from the straight line between Kretinga
# and an accepted village, a listing may be. A judgment call: change it here.
MAX_DISTANCE_KM = 3.0

# Location precisions (Listing.location_precision, added by #5) that say the point is only a
# town or village centre. Such a point cannot show where the house is, so location is unknown.
APPROXIMATE_PRECISIONS = frozenset({"city", "approximate"})

# Soft preferences. They never decide a verdict. They only appeared in the Claude Code
# command, not in HOUSE_CRITERIA.md, and are kept so the narrative can mention them.
SOFT_PREFERENCES = (
    "large plots (10+ ares)",
    "geothermal heating",
    "no main road noise",
    "real photos rather than renders",
)

NEW_BUILD_QUESTION = (
    "Is the house for sale a new build: a newly built house (naujos statybos), either "
    "finished within the last two years or under construction and sold as new, going by "
    "`title`, `description`, `year_built` and `is_new_construction`? Answer no for an older "
    "house, a house that was lived in or renovated, or an unfinished shell with no stated "
    "completion date."
)
PART_OF_HOUSE_QUESTION = (
    "Is what is for sale only a part of a house (namo dalis): one half or unit of a "
    "semi-detached, duplex or terraced house, rather than a whole detached house with its "
    "own plot, going by `title`, `description` and `property_type`?"
)


class Status(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class FilterResult:
    status: Status
    detail: str
    # How near a passing value is to the preferred one, 0 to 1. Only used for the score.
    closeness: float | None = None


@dataclass(frozen=True)
class Place:
    """An accepted place. ``lat``/``lon`` are None when no coordinates could be found: such a
    place is matched by name only. ``patterns`` are regexes over accent-stripped lower case."""

    name: str
    lat: float | None
    lon: float | None
    patterns: tuple[str, ...]


KRETINGA = Place(
    "Kretinga",
    55.8901,
    21.2424,
    (r"\bkretinga\b", r"\bkretingoje\b", r"\bkretingos\s+m\b"),
)

# Coordinates are the OpenStreetMap centres from the project's own Nominatim (Lithuania
# extract). Klonaiciai is not in that extract, so it is matched by name only.
PLACES: tuple[Place, ...] = (
    KRETINGA,
    Place("Dupulčiai", 55.8776, 21.2937, (r"\bdupulci",)),
    Place("Klonaičiai", None, None, (r"\bklonaic", r"\bklonal")),
    Place("Jakubavai (Jokūbavas)", 55.8276, 21.3423, (r"\bjakubav", r"\bjokubav")),
    Place("Raguviškiai", 55.8630, 21.3794, (r"\braguvisk",)),
    Place("Kartena", 55.9204, 21.4739, (r"\bkartena\b", r"\bkartenoje\b", r"\bkartenos\b")),
)

EARTH_RADIUS_KM = 6371.0088
_KM_PER_DEGREE = math.pi / 180 * EARTH_RADIUS_KM


# --- Text helpers --------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Lower case with Lithuanian diacritics removed, so "Dupulčiuose" matches "dupulci"."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


# "Kartenos sen." (an eldership) and "Kartenos g." (a street) name areas and roads, not the
# place the house stands in, so they are removed before matching.
_ADMIN_OR_STREET_RE = re.compile(
    r"\b[a-z]+\s+(?:seniunija|seniunijos|sen|g|gatve|gatves|al|aleja|pr|prospektas)\b\.?"
)


def _searchable(texts: Iterable[str | None]) -> str:
    joined = " , ".join(normalize(t) for t in texts if t)
    return _ADMIN_OR_STREET_RE.sub(" ", joined)


def matched_places(texts: Iterable[str | None]) -> list[Place]:
    haystack = _searchable(texts)
    return [p for p in PLACES if any(re.search(pat, haystack) for pat in p.patterns)]


# --- Geometry ------------------------------------------------------------------------------


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _distance_to_segment_km(
    lat: float, lon: float, a: tuple[float, float], b: tuple[float, float]
) -> float:
    """Distance from a point to the segment a-b. A local flat projection is exact enough at
    the tens of kilometres this works over."""
    cos_lat = math.cos(math.radians(a[0]))

    def xy(p_lat: float, p_lon: float) -> tuple[float, float]:
        return (p_lon - a[1]) * cos_lat * _KM_PER_DEGREE, (p_lat - a[0]) * _KM_PER_DEGREE

    px, py = xy(lat, lon)
    bx, by = xy(*b)
    length_sq = bx * bx + by * by
    t = 0.0 if length_sq == 0 else max(0.0, min(1.0, (px * bx + py * by) / length_sq))
    return math.hypot(px - t * bx, py - t * by)


def distance_to_accepted_area_km(lat: float, lon: float) -> tuple[float, str]:
    """Km to the nearest accepted centre or to the line between Kretinga and a village, and
    the name of what it is near."""
    best = (haversine_km(lat, lon, KRETINGA.lat, KRETINGA.lon), KRETINGA.name)
    for place in PLACES:
        if place.lat is None or place.lon is None:
            continue
        candidates = [(haversine_km(lat, lon, place.lat, place.lon), place.name)]
        if place is not KRETINGA:
            line = _distance_to_segment_km(
                lat, lon, (KRETINGA.lat, KRETINGA.lon), (place.lat, place.lon)
            )
            candidates.append((line, f"{KRETINGA.name}-{place.name}"))
        best = min([best, *candidates])
    return best


# --- Filters -------------------------------------------------------------------------------


def _number(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _closeness(value: float, preferred: float, low: float, high: float) -> float:
    reach = max(preferred - low, high - preferred)
    return max(0.0, 1.0 - abs(value - preferred) / reach)


def check_price(price: object, currency: str | None = "EUR") -> FilterResult:
    value = _number(price)
    if value is None:
        return FilterResult(Status.UNKNOWN, "kaina nenurodyta")
    if currency and currency.upper() != "EUR":
        return FilterResult(Status.UNKNOWN, f"kaina ne eurais ({currency})")
    if value > PRICE_MAX_EUR:
        return FilterResult(
            Status.FAIL, f"kaina {value:,.0f} EUR viršija {PRICE_MAX_EUR:,} EUR".replace(",", " ")
        )
    closeness = (
        1.0
        if value <= PRICE_TARGET_EUR
        else (PRICE_MAX_EUR - value) / (PRICE_MAX_EUR - PRICE_TARGET_EUR)
    )
    return FilterResult(Status.PASS, f"kaina {value:,.0f} EUR".replace(",", " "), closeness)


def check_area(area_sqm: object) -> FilterResult:
    value = _number(area_sqm)
    if value is None:
        return FilterResult(Status.UNKNOWN, "gyvenamasis plotas nenurodytas")
    if not AREA_MIN_SQM <= value <= AREA_MAX_SQM:
        return FilterResult(
            Status.FAIL,
            f"plotas {value:g} kv.m už ribų {AREA_MIN_SQM}-{AREA_MAX_SQM} kv.m",
        )
    closeness = _closeness(value, AREA_PREFERRED_SQM, AREA_MIN_SQM, AREA_MAX_SQM)
    return FilterResult(Status.PASS, f"plotas {value:g} kv.m", closeness)


def check_plot(plot_ares: object) -> FilterResult:
    value = _number(plot_ares)
    if value is None:
        return FilterResult(Status.UNKNOWN, "sklypo plotas nenurodytas")
    if not PLOT_MIN_ARES <= value <= PLOT_MAX_ARES:
        return FilterResult(
            Status.FAIL,
            f"sklypas {value:g} a už ribų {PLOT_MIN_ARES}-{PLOT_MAX_ARES} a",
        )
    closeness = _closeness(value, PLOT_PREFERRED_ARES, PLOT_MIN_ARES, PLOT_MAX_ARES)
    return FilterResult(Status.PASS, f"sklypas {value:g} a", closeness)


def check_location(
    lat: float | None,
    lon: float | None,
    texts: Sequence[str | None] = (),
    precision: str | None = None,
) -> FilterResult:
    """An accepted place named in ``texts`` (city, district, address) passes, unless reliable
    coordinates put the listing far away: then the evidence conflicts and it is unknown.
    Without a name, coordinates decide, unless they are only an approximate centre."""
    named = matched_places(texts)
    approximate = (precision or "").strip().lower() in APPROXIMATE_PRECISIONS
    has_point = lat is not None and lon is not None
    usable = has_point and not approximate

    if usable:
        distance, near = distance_to_accepted_area_km(lat, lon)  # type: ignore[arg-type]
        within = distance <= MAX_DISTANCE_KM
        if named and not within:
            return FilterResult(
                Status.UNKNOWN,
                f"adresas nurodo {named[0].name}, bet koordinatės {distance:.1f} km nuo {near}",
            )
        if within:
            return FilterResult(Status.PASS, f"{distance:.1f} km nuo {near}")
        return FilterResult(
            Status.FAIL, f"{distance:.1f} km nuo {near}, riba {MAX_DISTANCE_KM:g} km"
        )
    if named:
        return FilterResult(Status.PASS, f"vietovė: {named[0].name}")
    if approximate:
        return FilterResult(Status.UNKNOWN, "koordinatės apytikslės, vietovė neatpažinta")
    return FilterResult(Status.UNKNOWN, "vieta nežinoma")


def type_result(value: bool | None, question: str) -> FilterResult:
    """A yes/no answer as a filter. ``question`` is "new_build" (yes passes) or "whole_house"
    (this is the answer to *is part of a house*, so yes fails)."""
    if value is None:
        text = {"new_build": "naujos statybos nežinoma", "whole_house": "namo dalis nežinoma"}
        return FilterResult(Status.UNKNOWN, text[question])
    if question == "new_build":
        return FilterResult(
            Status.PASS if value else Status.FAIL,
            "naujos statybos" if value else "ne naujos statybos",
        )
    return FilterResult(
        Status.FAIL if value else Status.PASS,
        "namo dalis" if value else "visas namas",
    )


# --- Keyword fallback for the yes/no judgments ---------------------------------------------

_NEW_BUILD_RE = re.compile(
    r"\bnaujos?\s+statyb|\bnauja\s+statyba|\bnaujas\s+namas|\bnauji\s+namai|\bnew\s+build"
)
_PART_OF_HOUSE_RE = re.compile(r"\bnamo\s+(?:dalis|dali|dalies|dalyje|puse)\b|\bdvibutis")
# A house finished this many years ago is no longer sold as new.
NEW_BUILD_MAX_AGE_YEARS = 2


def keyword_new_build(
    is_new_construction: bool, year_built: int | None, text: str, current_year: int
) -> bool | None:
    """The portal's flag or new-build wording says yes; a clearly old year says no; otherwise
    abstain. The flag defaults to False when a portal says nothing, so False alone is no answer."""
    if is_new_construction or _NEW_BUILD_RE.search(normalize(text)):
        return True
    if year_built and year_built < current_year - NEW_BUILD_MAX_AGE_YEARS:
        return False
    return None


def keyword_part_of_house(property_type: str | None, text: str) -> bool | None:
    if property_type == "part_of_house" or _PART_OF_HOUSE_RE.search(normalize(text)):
        return True
    return False


# --- Verdict and score ---------------------------------------------------------------------

FILTER_ORDER = ("price", "area", "plot", "location", "new_build", "whole_house")
_HARD_FILTER_KEYS = {
    "price": "price_ok",
    "area": "area_ok",
    "plot": "plot_ok",
    "location": "location_ok",
}


@dataclass(frozen=True)
class Decision:
    verdict: str
    score: float
    summary: str
    failed: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    hard_filter_results: dict[str, bool] = field(default_factory=dict)


def decide(results: Mapping[str, FilterResult]) -> Decision:
    """Verdict and score from the filter results alone, following the old prompt's guide:
    skip 0.0-0.2, review 0.4-0.8, match 0.8-1.0.

    - any fail: skip, 0.2 times the share of filters that passed;
    - no fail but some unknown: review, 0.4 to 0.8 by how many are settled;
    - all pass: match, 0.8 to 1.0 by how near price, area and plot are to the preferred values.
    """
    names = [n for n in FILTER_ORDER if n in results]
    failed = [n for n in names if results[n].status == Status.FAIL]
    unknown = [n for n in names if results[n].status == Status.UNKNOWN]
    passed = [n for n in names if results[n].status == Status.PASS]

    if failed:
        score = 0.2 * len(passed) / len(names)
        summary = (
            "Praleista pagal kriterijus: " + "; ".join(results[n].detail for n in failed) + "."
        )
        verdict = "skip"
    elif unknown:
        score = 0.4 + 0.4 * (len(passed) + 0.5 * len(unknown)) / len(names)
        summary = "Reikia patikrinti: " + "; ".join(results[n].detail for n in unknown) + "."
        verdict = "review"
    else:
        closeness = [
            results[n].closeness
            for n in ("price", "area", "plot")
            if results[n].closeness is not None
        ]
        score = 0.8 + 0.2 * (sum(closeness) / len(closeness) if closeness else 1.0)
        summary = "Atitinka visus kriterijus."
        verdict = "match"

    return Decision(
        verdict=verdict,
        score=round(score, 3),
        summary=summary,
        failed=failed,
        unknown=unknown,
        hard_filter_results=hard_filter_results(results),
    )


def hard_filter_results(results: Mapping[str, FilterResult]) -> dict[str, bool]:
    """The old ``hard_filter_results`` shape (booleans the UI renders). Unknown is left out,
    because the UI would draw a missing or null value as a fail."""
    out: dict[str, bool] = {}
    for name, key in _HARD_FILTER_KEYS.items():
        if name in results and results[name].status != Status.UNKNOWN:
            out[key] = results[name].status == Status.PASS
    type_parts = [results[n].status for n in ("new_build", "whole_house") if n in results]
    if Status.FAIL in type_parts:
        out["type_ok"] = False
    elif type_parts and Status.UNKNOWN not in type_parts:
        out["type_ok"] = True
    return out


# --- Text for prompts and docs -------------------------------------------------------------


def describe() -> str:
    """The criteria as prompt text. Prompts and the Claude Code command use this verbatim."""
    places = ", ".join(p.name for p in PLACES)
    return (
        "## Buyer's Hard Criteria\n"
        "\n"
        f"- **Budget**: target ~{PRICE_TARGET_EUR:,} EUR, max {PRICE_MAX_EUR:,} EUR. "
        f"Skip above {PRICE_MAX_EUR:,} EUR.\n"
        '- **Type**: new build only. "Namo dalis" (part of a house) = always skip.\n'
        f"- **Living area**: {AREA_PREFERRED_SQM} kv.m preferred, acceptable "
        f"{AREA_MIN_SQM}-{AREA_MAX_SQM} kv.m.\n"
        f"- **Plot size**: {PLOT_PREFERRED_ARES} ares (arų) preferred, acceptable "
        f"{PLOT_MIN_ARES}-{PLOT_MAX_ARES} ares.\n"
        f"- **Location**: {places}, or a place between these and Kretinga "
        f"(within {MAX_DISTANCE_KM:g} km of one of them or of the line from Kretinga to one of "
        "them). Anything outside = skip.\n"
        "\n"
        "A value that is missing from the listing is unknown, not a fail. "
        "Soft preferences (never a reason to skip): " + ", ".join(SOFT_PREFERENCES) + "."
    )


if __name__ == "__main__":
    print(describe())
