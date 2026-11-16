"""Strip contact data from scraped listings. Pure Python, no Django imports.

CLAUDE.md rule 6: phone numbers and contact info are never stored. Used by the scrapers'
ScrubPipeline (at ingest) and by the listings data migration (existing rows), so the same
rules apply to both. Every function is idempotent: scrubbing scrubbed data changes nothing.
"""

from __future__ import annotations

import json
import re
from typing import Any

# A key is contact data if its lowercased name contains any of these.
CONTACT_KEY_PARTS = (
    "contact",
    "seller",
    "phone",
    "telephone",
    "telefon",
    "mobile",
    "mobil",
    "email",
    "e-mail",
    "e_mail",
    "kontakt",
    "pardav",  # pardavėjas = seller
)
# Short keys that would be too loose as substrings ("tel" is inside "hotel").
CONTACT_KEYS_EXACT = {"tel", "tel.", "mob", "mob.", "agent", "owner", "advertiser"}

# Lithuanian numbers: +370 / 00370 / bare 370 followed by 8 digits, or national 8 xxx xxxxx.
# Separators (space - . parentheses) may sit between any two digits.
_SEP = r"[\s\-.()]*"
_PHONE_PATTERNS = (
    # +370 or 00370; the digit tail is optional so a truncated "+370" never survives
    rf"(?<!\d)(?:\+\s*|00\s*)370(?:{_SEP}\d){{0,8}}",
    # 370 without the plus, only when a full 8-digit tail follows
    rf"(?<![\d+])370(?:{_SEP}\d){{8}}(?!\d)",
    # national format: 8 then 8 more digits (8 6xx xxxxx, 8 5 xxx xxxx, 861234567)
    rf"(?<![\d.,+])8(?:{_SEP}\d){{8}}(?!\d)",
)
# An optional "tel.:" style label is removed with the number it introduced.
_LABEL = r"(?:\b(?:tel|mob|telefonas|tel\.?\s*nr|kontaktai|kreiptis)\b\.?\s*:?\s*)?"
PHONE_RE = re.compile(
    _LABEL + "(?:" + "|".join(f"(?:{p})" for p in _PHONE_PATTERNS) + ")", re.IGNORECASE
)
EMAIL_RE = re.compile(r"[\w.+\-]+\s?@\s?[\w\-]+(?:\.[\w\-]+)+")

MAX_RAW_DATA_BYTES = 20_000
TRUNCATED_KEY = "_truncated_keys"


def scrub_text(text: str | None) -> str:
    """Remove phone numbers and email addresses from free text."""
    if not text:
        return text or ""
    cleaned = PHONE_RE.sub("", text)
    cleaned = EMAIL_RE.sub("", cleaned)
    if cleaned == text:
        return text
    # tidy the gaps the removals left, without touching line breaks
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"[ \t]+([,.;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]+$", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"^[ \t]*[,;:]\s*", "", cleaned, flags=re.MULTILINE)
    return cleaned.strip()


def is_contact_key(key: Any) -> bool:
    name = str(key).lower().strip()
    return name in CONTACT_KEYS_EXACT or any(part in name for part in CONTACT_KEY_PARTS)


def scrub_value(value: Any) -> Any:
    """Recursively drop contact keys and scrub every string inside a JSON-like value."""
    if isinstance(value, dict):
        return {k: scrub_value(v) for k, v in value.items() if not is_contact_key(k)}
    if isinstance(value, list):
        return [scrub_value(v) for v in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode())


def cap_size(data: dict, max_bytes: int = MAX_RAW_DATA_BYTES) -> dict:
    """Drop the largest top-level keys until the JSON fits in max_bytes.

    The dropped key names are kept under ``_truncated_keys`` so the loss is visible.
    """
    if _size(data) <= max_bytes:
        return data
    kept = dict(data)
    dropped = list(kept.pop(TRUNCATED_KEY, []))
    while kept and _size({**kept, TRUNCATED_KEY: dropped}) > max_bytes:
        largest = max(kept, key=lambda k: _size(kept[k]))
        kept.pop(largest)
        dropped.append(str(largest))
    kept[TRUNCATED_KEY] = dropped
    return kept


def scrub_raw_data(data: Any, max_bytes: int = MAX_RAW_DATA_BYTES) -> dict:
    """Contact keys removed, phones/emails scrubbed from values, size capped."""
    if not isinstance(data, dict):
        return {}
    return cap_size(scrub_value(data), max_bytes)
