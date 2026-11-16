"""Small parsers for the labelled values Lithuanian portals print ("66,39 m²", "9 a", "1988").

Shared by the spiders that read values off rendered pages (alio, skelbiu). They return clean
numbers or None, so an item never carries a half-parsed string for the pipeline to guess at.
"""

from __future__ import annotations

import re

BUILDING_TYPE_MAP = {
    "mūrinis": "brick",
    "blokinis": "block",
    "medinis": "wooden",
    "monolitinis": "monolithic",
    "rąstinis": "log",
    "karkasinis": "frame",
}

ENERGY_CLASS = re.compile(r"^[A-G]\+{0,2}$")
# Rough box around Lithuania; a coordinate outside it is a bad value, not a location.
LT_LAT, LT_LON = (53.8, 56.6), (20.9, 26.9)

_NUMBER = re.compile(r"\d+(?:[\s ]\d{3})*(?:[.,]\d+)?")
_YEAR = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\b")


def number(text: object) -> float | None:
    """The first number in `text`: "66,39 m²" -> 66.39, "209 000 €" -> 209000.0."""
    match = _NUMBER.search(str(text or ""))
    if not match:
        return None
    return float(re.sub(r"[\s ]", "", match.group()).replace(",", "."))


def whole_number(text: object) -> int | None:
    value = number(text)
    return int(value) if value is not None else None


def ares(text: object) -> float | None:
    """A plot size in ares (1 ha = 100 a): "15,80 a" -> 15.8, "0,25 ha" -> 25.0."""
    value = number(text)
    if value is None:
        return None
    return value * 100 if re.search(r"\bha\b", str(text).lower()) else value


def year(text: object) -> int | None:
    """The first plausible build year: "1982, 2024 renovuotas" -> 1982."""
    match = _YEAR.search(str(text or ""))
    return int(match.group()) if match else None


def lt_point(lat: object, lon: object) -> tuple[float, float] | None:
    """(lat, lon) when the portal gave a usable point inside Lithuania, else None."""
    try:
        la, lo = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if LT_LAT[0] <= la <= LT_LAT[1] and LT_LON[0] <= lo <= LT_LON[1]:
        return la, lo
    return None
