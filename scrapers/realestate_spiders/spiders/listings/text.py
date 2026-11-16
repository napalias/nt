"""Small text helpers the agency spiders share: numbers and contact-free descriptions.

CLAUDE.md rule 6: no phone numbers or contact info are stored. `ScrubPipeline` removes phone
numbers and emails from the text, but it cannot know that a line is an agent's name or a
"call me" sentence, and the agency sites sign their ads with exactly that. So the spiders cut
those lines out before the item exists, and the pipeline stays the second net.
"""

from __future__ import annotations

import re

from apps.listings.scrub import EMAIL_RE, PHONE_RE

NUMBER = re.compile(r"\d[\d\s ]*(?:[.,]\d+)?")

# A line that is a contact label ("El.p.: ...", "Mob.: ...", "tel. Nr. ...") or a web link.
CONTACT_LINE = re.compile(
    r"^\W*(?:el\.?\s*(?:p\b|pašt)|e-?mail|mob\b|tel\b|telefonas|telefono\s+nr|kontakt\w*|www\.|https?://)"
    r"|kreipkit|kreiptis|skambin|teirau",
    re.IGNORECASE,
)
# A rule drawn with underscores or dashes between the ad and its signature.
SEPARATOR_LINE = re.compile(r"^[_\-=*~\s]{5,}$")
# "Nekilnojamojo turto ekspertė", "Pardavimų vadybininkė": the job title that sits under a name.
ROLE_LINE = re.compile(
    r"ekspert|brokeri|konsultant|vadybinink|savininkė|savininkas|agent|direktor",
    re.IGNORECASE,
)
# Two or three capitalised words, nothing else: what an agent's name looks like on its own line.
NAME_LINE = re.compile(r"^[A-ZĄČĘĖĮŠŲŪŽ][a-ząčęėįšųūž]+(?:\s+[A-ZĄČĘĖĮŠŲŪŽ][a-ząčęėįšųūž-]+){1,2}$")


def to_number(text: object) -> float | None:
    """First number in `text` as a float: "74 000 €" -> 74000.0, "21,68 a" -> 21.68, else None.

    A space inside a number is a thousands separator ("74 000"); a comma or dot with digits
    after it is the decimal mark."""
    if isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        return float(text)
    match = NUMBER.search(str(text or ""))
    if not match:
        return None
    digits = re.sub(r"[\s ]", "", match.group(0)).replace(",", ".")
    try:
        return float(digits)
    except ValueError:
        return None


def clean_lines(lines: list[str]) -> str:
    """Join description lines, dropping contact lines, an agent's name and job title, and any
    phone number or email left inside a line that is otherwise kept."""
    kept: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            if kept and kept[-1]:
                kept.append("")  # one blank line between paragraphs
            continue
        if CONTACT_LINE.search(line) or SEPARATOR_LINE.match(line):
            continue
        if ROLE_LINE.search(line) and len(line) < 60:
            # the line above a job title is the person's name
            if kept and NAME_LINE.match(kept[-1]):
                kept.pop()
            continue
        line = EMAIL_RE.sub("", PHONE_RE.sub("", line)).strip()
        if line:
            kept.append(line)
    while kept and not kept[-1]:
        kept.pop()
    return "\n".join(kept)


def with_sav(place: str) -> str:
    """ "Kretingos r." -> "Kretingos r. sav." so the geocoder recognises a district."""
    place = place.strip()
    return f"{place} sav." if place.endswith(" r.") else place
