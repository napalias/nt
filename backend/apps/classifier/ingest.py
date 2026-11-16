"""Jev's small typed questions around scraping (#18): the questions, how they are asked, and
what each caller does with the answer.

One place for all five so no caller words a question twice and the ``off`` fallback of each
sits next to its question. Evaluation (``services.py``) and dedup (``dedup.py``) ask their own
questions; ``is_new_build`` is shared with evaluation (same name, same text from
``criteria.py``), so one threshold override in ``settings.JEV_THRESHOLDS`` covers both.

======================  =========  ====================================================
question                kind       where it is asked and what the fallback is
======================  =========  ====================================================
``is_house_sale_ad``    ingest     ``JevScreenPipeline`` for mixed-feed sources. The
                                   fallback is the spider's own code filter.
``is_new_build``        ingest     the same pipeline, every listing. The fallback is the
                                   portal's flag and the keyword list in ``criteria.py``.
``wall_kind``           wall       ``BlockDetectionMiddleware`` for a short HTTP 200 page
                                   that no code marker recognised. Fallback: "none".
``page_expired``        expired    ``check_listing_validity`` for a 200 page. Fallback:
                                   ``tasks.STALE_MARKERS``.
``needs_cleanup``       cleanup    ``services.cleanup_batch``. Fallback: the old
                                   marketing-words regex.
======================  =========  ====================================================

What each mode does here (``settings.JEV_MODE``):

- ``off``: today's behaviour, byte for byte. Jev is not called and nothing is logged. Spiders
  skip what their code filter rejects and no screen looks at anything.
- ``shadow``: the fallback decides and the result is what ``off`` would have produced. Jev is
  asked too and both answers go to ``JudgmentLog`` with their agreement. Nothing is filtered
  or changed because of Jev. (Spiders hand their code-filter rejects to the pipeline instead
  of skipping them, so Jev's answer on the ads the filter rejected is logged as well; the
  pipeline drops them just as the spider would have.)
- ``on``: Jev decides each question whose probability clears its threshold; the fallback
  decides the rest. A "not a house ad" answer drops the item; a new-build answer sets the
  flag; a wall kind is recorded as a block; an expired page deactivates the listing; a
  needs-cleanup "yes" sends the description to Claude Haiku.

Only the *decision* moves to Jev. The description rewrite itself stays on
``settings.CLAUDE_FAST_MODEL`` (``services.cleanup_description``).

Pure Python plus ``judge``: the scrapers container imports it.
"""

from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from apps.classifier import criteria, judge
from apps.classifier.judge import Question
from apps.listings.blocks import BlockKind

KIND_INGEST = "ingest"
KIND_WALL = "wall"
KIND_EXPIRED = "expired"
KIND_CLEANUP = "cleanup"

# --- The questions -------------------------------------------------------------------------

HOUSE_SALE_AD = Question.yes_no(
    "is_house_sale_ad",
    "Is this an ad offering a house (a detached house, farmstead or cottage, whole or "
    "unfinished) for sale, going by `title`, `description` and `property_type`? Answer no for "
    "an ad for rent, an ad that wants to buy, a flat, a plot of land with no house, building "
    "materials, a construction or other service, or anything else that is not a house for sale.",
    yes="A house for sale",
    no="Anything else: rent, wanted, flat, land only, materials, services",
)

# Shared with evaluation (services.py): the same name and text, so one threshold covers both.
IS_NEW_BUILD = Question.yes_no("is_new_build", criteria.NEW_BUILD_QUESTION)

WALL_KINDS: dict[str, str | None] = {
    "none": "A normal page: listings, a listing, an empty result list, an ordinary error page",
    BlockKind.CAPTCHA.value: "A captcha or 'prove you are human' check",
    BlockKind.CHALLENGE.value: "A bot check or 'checking your browser' interstitial",
    BlockKind.LOGIN.value: "A page asking the visitor to log in or register to see the content",
    BlockKind.FORBIDDEN.value: "An 'access denied' or 'forbidden' page",
    BlockKind.RATE_LIMITED.value: "A 'too many requests' or 'try again later' page",
}
WALL_KIND = Question.choice(
    "wall_kind",
    "We fetched this page with HTTP 200 while collecting house ads, and it is short. Going by "
    "`title` and `text`, is the site showing us a wall instead of its content, and which kind?",
    WALL_KINDS,
)

PAGE_EXPIRED = Question.yes_no(
    "page_expired",
    "This is the page of a property ad we saw earlier, fetched today with HTTP 200. Going by "
    "`title`, `heading` and `text`, does the page say the ad is gone: removed, expired, sold, "
    "withdrawn or no longer available? Answer no if the ad is still shown for sale.",
    yes="The ad is gone",
    no="The ad is still live",
)

NEEDS_CLEANUP = Question.yes_no(
    "needs_cleanup",
    "Does `description` carry marketing noise that should be removed before a buyer reads it: "
    "fake urgency, exaggerated praise, text in capitals, repeated slogans, or the agent "
    "advertising themselves? Answer no for a plain factual description, even a short or "
    "badly written one.",
    yes="Marketing noise to remove",
    no="Factual text",
)

# The old marketing-words prefilter of cleanup_batch, now the `off` fallback and the
# candidate query in off mode. Postgres regex syntax.
CLEANUP_FLUFF_PATTERN = r"(!{2,}|SKUBIAI|PUIKI INVESTICIJA|Nepraleiskite)"
_CLEANUP_FLUFF_RE = re.compile(CLEANUP_FLUFF_PATTERN)

# --- Mode helpers --------------------------------------------------------------------------


def screens_active() -> bool:
    """Whether the screens run at all (shadow or on). Off is today's path."""
    return judge.current_mode() != "off"


# --- Page text -----------------------------------------------------------------------------

# A real listing or index page carries thousands of visible characters; a wall or an empty
# shell a few hundred. Only pages shorter than this are worth a wall question.
AMBIGUOUS_MAX_TEXT_CHARS = 600
WALL_TEXT_CHARS = 1500
EXPIRED_TEXT_CHARS = 2000

_DROP_BLOCKS_RE = re.compile(r"<(script|style|noscript|svg)\b.*?</\1\s*>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)


def _squash(text: str) -> str:
    return " ".join(html.unescape(text).split())


def visible_text(markup: str) -> str:
    """The text a visitor would read: no scripts, styles or tags, whitespace collapsed."""
    return _squash(_TAG_RE.sub(" ", _DROP_BLOCKS_RE.sub(" ", markup or "")))


def _first(pattern: re.Pattern[str], markup: str) -> str:
    match = pattern.search(markup or "")
    return _squash(_TAG_RE.sub(" ", match.group(1))) if match else ""


# --- Ingest screens: house ad and new build -------------------------------------------------


@dataclass(frozen=True)
class ScreenVerdict:
    """What the pipeline does with one scraped listing.

    ``drop_reason`` is non-empty when the item must be dropped; it is the text the /scans page
    shows, so it names the decider ("Jev" or "keyword filter") and carries no per-item detail
    (reasons are grouped by text). ``is_new_construction`` is set only when Jev's judgment
    applies (mode ``on``); None leaves the item's own flag alone.
    """

    drop_reason: str = ""
    is_new_construction: bool | None = None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return None


def _item_state(item: Mapping[str, Any]) -> dict[str, Any]:
    """The judge's trimmed state for a scraped item (it names the address `address`)."""
    return judge.listing_state({**dict(item), "address_raw": item.get("address")})


def _item_subject(item: Mapping[str, Any]) -> str:
    """source:id (the id may not be set yet when the screen runs, then a digest of the URL)."""
    ident = str(item.get("source_id") or "").strip()
    if not ident:
        ident = hashlib.sha1(str(item.get("url") or "").encode()).hexdigest()[:16]
    return f"{str(item.get('source') or '?')[:32]}:{ident[:30]}"


def screen_listing(
    item: Mapping[str, Any],
    *,
    mixed_feed: bool,
    house_heuristic: bool | None,
    current_year: int | None = None,
) -> ScreenVerdict:
    """Judge one scraped listing before it is validated, geocoded or stored.

    ``mixed_feed``: the source lists more than houses (a keyword search, a general board), so
    "is this a house for sale?" is asked. ``house_heuristic`` is the spider's own code filter
    for that: True it accepted the ad, False it would have skipped it, None it has no opinion.
    """
    year = current_year or date.today().year
    mode = judge.current_mode()

    if not mixed_feed:
        house_heuristic = None  # nothing to ask or to drop
    if mode == "off":
        # Today's path: a rejecting code filter drops the ad, as the spider would have skipped
        # it; nothing else changes. (Spiders only pass such ads on when a mode is switched on.)
        if house_heuristic is False:
            return ScreenVerdict(drop_reason=_house_reason("keyword filter"))
        return ScreenVerdict()

    text = f"{item.get('title') or ''}\n{item.get('description') or ''}"
    questions = [IS_NEW_BUILD] + ([HOUSE_SALE_AD] if mixed_feed else [])

    def fallback(pending: Sequence[Question]) -> dict[str, Any]:
        answers = {
            HOUSE_SALE_AD.name: house_heuristic,
            IS_NEW_BUILD.name: criteria.keyword_new_build(
                bool(item.get("is_new_construction")),
                _int_or_none(item.get("year_built")),
                text,
                year,
            ),
        }
        return {q.name: answers[q.name] for q in pending}

    result = judge.ask(
        KIND_INGEST,
        _item_subject(item),
        _item_state(item),
        questions,
        fallback,
        fallback_model="keyword",
    )

    drop_reason = ""
    if mixed_feed:
        house = result[HOUSE_SALE_AD.name]
        if house.value is False:
            drop_reason = _house_reason("Jev" if house.decided_by == "jev" else "keyword filter")
    is_new = result[IS_NEW_BUILD.name].value if result.mode == "on" else None
    return ScreenVerdict(drop_reason=drop_reason, is_new_construction=is_new)


def _house_reason(decider: str) -> str:
    return f"Not a house for sale ({decider})"


# --- Wall kind -------------------------------------------------------------------------------


def looks_ambiguous(markup: str) -> bool:
    """A 200 page with so little to read that a wall is possible: worth one wall question."""
    return len(visible_text(markup)) < AMBIGUOUS_MAX_TEXT_CHARS


def judge_wall(url: str, markup: str) -> BlockKind | None:
    """The block kind Jev decides a short 200 page shows, or None.

    Called after the code markers found nothing. Only ``on`` mode returns a kind; ``shadow``
    logs Jev's answer beside the code's ("none") and returns None, as ``off`` does without
    asking. Does not check that the page is short: the caller does (``looks_ambiguous``).
    """
    if judge.current_mode() == "off":
        return None
    parts = urlsplit(url)
    digest = hashlib.sha1(f"{parts.netloc}{parts.path}".encode()).hexdigest()[:16]
    state = {
        "http_status": 200,
        "url_path": parts.path[:200],
        "title": _first(_TITLE_RE, markup)[:200],
        "text": visible_text(markup)[:WALL_TEXT_CHARS],
    }
    result = judge.ask(
        KIND_WALL,
        f"{parts.netloc[:40]}:{digest}",
        state,
        [WALL_KIND],
        lambda pending: {WALL_KIND.name: "none"},  # the code markers saw no wall
        fallback_model="markers",
    )
    value = result[WALL_KIND.name].value
    if result.mode == "on" and value and value != "none":
        return BlockKind(value)
    return None


# --- Expired page ----------------------------------------------------------------------------


def page_expired(listing_pk: int, markup: str, markers: Sequence[str]) -> bool:
    """Whether the HTTP 200 page of a listing says the ad is gone.

    ``markers`` are the old lower-case phrases (``tasks.STALE_MARKERS``): the answer in
    ``off`` and ``shadow``, and the fallback in ``on``. In ``shadow`` Jev is asked as well and
    logged; only in ``on`` can it change the answer.
    """
    marker_hit = any(marker in (markup or "").lower() for marker in markers)
    if judge.current_mode() == "off":
        return marker_hit
    state = {
        "title": _first(_TITLE_RE, markup)[:200],
        "heading": _first(_H1_RE, markup)[:200],
        "text": visible_text(markup)[:EXPIRED_TEXT_CHARS],
    }
    result = judge.ask(
        KIND_EXPIRED,
        listing_pk,
        state,
        [PAGE_EXPIRED],
        lambda pending: {PAGE_EXPIRED.name: marker_hit},
        fallback_model="markers",
    )
    return result[PAGE_EXPIRED.name].value is True


# --- Needs cleanup ---------------------------------------------------------------------------

# Listings shadow mode asks about that the old regex did not flag, per run: enough to compare
# Jev with the regex on ads the regex calls clean, few enough to stay cheap.
SHADOW_EXTRA_PER_RUN = 10
MIN_CLEANUP_CHARS = 20
# Most unflagged listings one run weighs up; the table is small, this only bounds a runaway.
MAX_CLEANUP_POOL = 500


def _cleanup_subject(listing: Any) -> str:
    """Listing id plus its content hash: a listing is judged again only after it changes."""
    return f"{listing.pk}:{(listing.content_hash or '')[:16]}"


def needs_cleanup(listing: Any) -> bool:
    """Whether this listing's description should go to the cleanup rewrite."""
    regex_hit = bool(_CLEANUP_FLUFF_RE.search(listing.description or ""))
    if judge.current_mode() == "off":
        return regex_hit
    result = judge.ask(
        KIND_CLEANUP,
        _cleanup_subject(listing),
        judge.listing_state(listing, ("title", "description")),
        [NEEDS_CLEANUP],
        lambda pending: {NEEDS_CLEANUP.name: regex_hit},
        fallback_model="regex",
    )
    return result[NEEDS_CLEANUP.name].value is True


def cleanup_candidates(limit: int) -> list[Any]:
    """Listings cleanup_batch should look at, at most ``limit`` flagged by the old regex plus
    the not-yet-judged others Jev is asked about.

    ``off``: exactly the old query, the regex matches. Otherwise the regex matches come first
    (so shadow cleans exactly what off would), then listings the regex did not flag that
    have no cleaned text yet and no judgment since their last change: ``limit`` of them in
    ``on`` (Jev may find noise the regex missed), ``SHADOW_EXTRA_PER_RUN`` in ``shadow``.
    """
    from apps.classifier.models import JudgmentLog
    from apps.listings.models import Listing

    flagged = list(
        Listing.objects.filter(is_active=True, description__regex=CLEANUP_FLUFF_PATTERN)[:limit]
    )
    mode = judge.current_mode()
    if mode == "off":
        return flagged

    extra_limit = limit if mode == "on" else SHADOW_EXTRA_PER_RUN
    flagged_ids = {listing.pk for listing in flagged}
    pool = (
        Listing.objects.filter(is_active=True, description_clean="")
        .exclude(pk__in=flagged_ids)
        .exclude(description="")
        .order_by("pk")[:MAX_CLEANUP_POOL]
    )
    others = [listing for listing in pool if len(listing.description) >= MIN_CLEANUP_CHARS]
    judged = set(
        JudgmentLog.objects.filter(
            kind=KIND_CLEANUP,
            question=NEEDS_CLEANUP.name,
            subject_id__in=[_cleanup_subject(listing) for listing in others],
        ).values_list("subject_id", flat=True)
    )
    fresh = [listing for listing in others if _cleanup_subject(listing) not in judged]
    return flagged + fresh[:extra_limit]
