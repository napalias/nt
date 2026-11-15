"""Turn Lithuanian listing addresses into queries Nominatim can match.

Portals write places in the locative case ("Stančiuose" = "in Stančiai") and abbreviate
municipalities ("Klaipėdos r. sav."). Nominatim only knows nominative names, so the raw
address matches nothing. geocode_queries() returns candidate queries, most specific
first; the caller tries them in order and keeps the first hit.
"""

import re

# Street-type abbreviations used by Lithuanian portals (gatvė, alėja, prospektas, ...).
STREET_SUFFIXES = ("g.", "al.", "pr.", "pl.", "skg.", "kel.", "tak.", "a.")

# Locative ending → nominative ending(s). Longest endings first so "iuose" wins over "uose".
LOCATIVE_ENDINGS: list[tuple[str, tuple[str, ...]]] = [
    ("iuose", ("iai",)),  # Stančiuose → Stančiai
    ("uose", ("ai",)),  # Bikūnuose → Bikūnai
    ("iose", ("ios",)),
    ("ėse", ("ės",)),  # Sakiškėse → Sakiškės
    ("ose", ("os",)),
    ("yse", ("ys",)),  # Vėliučionyse → Vėliučionys
    ("oje", ("a",)),  # Kretingoje → Kretinga
    ("ėje", ("ė",)),  # Plungėje → Plungė
    ("yje", ("ys", "is")),
    ("uje", ("us",)),  # Vilniuje → Vilnius
]

# Municipality abbreviations → the full names Nominatim stores.
MUNICIPALITY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\s+(?:r\.|raj\.|rajono)\s*sav\.?$", re.IGNORECASE), " rajono savivaldybė"),
    (re.compile(r"\s+(?:m\.|miesto)\s*sav\.?$", re.IGNORECASE), " miesto savivaldybė"),
    (re.compile(r"\s+sav\.?$", re.IGNORECASE), " savivaldybė"),
]

MIN_STEM = 3  # don't strip endings off very short words


def nominative_variants(word: str) -> list[str]:
    """Nominative forms to try for one place-name word (the word itself if not locative)."""
    for ending, replacements in LOCATIVE_ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= MIN_STEM:
            stem = word[: -len(ending)]
            return [stem + r for r in replacements]
    return [word]


def _municipality(part: str) -> str | None:
    for pattern, full in MUNICIPALITY_PATTERNS:
        if pattern.search(part):
            return pattern.sub(full, part).strip()
    return None


def _is_street(part: str) -> bool:
    return part.lower().endswith(STREET_SUFFIXES)


def geocode_queries(address: str, city: str = "") -> list[str]:
    """Candidate Nominatim queries for an address, most specific first, original last."""
    street = ""
    municipality = ""
    place_words: list[str] = []

    for raw in address.split(","):
        part = raw.strip()
        if not part:
            continue
        muni = _municipality(part)
        if muni:
            municipality = muni
        elif _is_street(part):
            street = part
        else:
            place_words.extend(part.split())

    # "Vilniuje Kairėnuose" → specific place "Kairėnai", broader place "Vilnius".
    places = [nominative_variants(w) for w in place_words]
    specific_variants = places[-1] if places else []
    broader = [variants[0] for variants in places[:-1]]

    candidates: list[str] = []

    def add(*parts: str) -> None:
        query = ", ".join(p for p in parts if p)
        if query and query not in candidates:
            candidates.append(query)

    for specific in specific_variants:
        add(street, specific, *broader, municipality)
        add(specific, *broader, municipality)
        add(street, specific)
        if street:
            for wider in broader:
                add(street, wider)
        add(specific, *broader)

    if city and not specific_variants:
        add(street, city)
        add(city)

    add(address.strip())
    return candidates
