"""Drop the "contact me" lines from a listing description before it becomes an item.

`apps.listings.scrub` removes phone numbers and emails from text, but a line such as
"Dėl apžiūros kreipkitės – <first name> +370 ..." keeps the agent's name once the number is gone.
CLAUDE.md rule 6 is no contact info, so a line that has a phone number or email and also reads as a
contact request is dropped whole. A line with a number but no such cue is left for the scrub
pipeline, which removes the number and keeps the rest.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from apps.listings.scrub import EMAIL_RE, PHONE_RE

CONTACT_CUE = re.compile(
    r"kreipk|kreipt|skambin|skamb|\btel\b|\btel\.|telefon|kontakt|susisiek|dėl apžiūros|"
    r"dėl informacijos|informacija tel|\bmob\b|\bmob\.",
    re.IGNORECASE,
)


def is_contact_line(line: str) -> bool:
    return bool(CONTACT_CUE.search(line)) and bool(PHONE_RE.search(line) or EMAIL_RE.search(line))


def without_contact_lines(lines: Iterable[str]) -> list[str]:
    return [line for line in lines if not is_contact_line(line)]
