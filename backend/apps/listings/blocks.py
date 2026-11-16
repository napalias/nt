"""Why a site stopped answering us: the block kinds a crawl can record.

Plain Python on purpose (no Django import), like sources.py: the scrapers' block-detection
downloader middleware imports it to name its Scrapy stats, and the backend reads the same
stats back into ScrapeRun.block_kind (apps/listings/scrape_runs.py).
"""

from __future__ import annotations

from enum import StrEnum


class BlockKind(StrEnum):
    CAPTCHA = "captcha"  # PerimeterX, DataDome or another "prove you are human" page
    CHALLENGE = "challenge"  # Cloudflare "Just a moment", Incapsula: a bot check page
    LOGIN = "login"  # a login form or HTTP 401 instead of the content
    ROBOTS = "robots"  # robots.txt forbids a start URL, so the crawl never starts
    FORBIDDEN = "forbidden"  # HTTP 403 with no recognisable challenge page
    RATE_LIMITED = "rate_limited"  # HTTP 429


# Labels for the admin and the model choices (Lithuanian, like the rest of the admin).
LABELS: dict[BlockKind, str] = {
    BlockKind.CAPTCHA: "Captcha",
    BlockKind.CHALLENGE: "Roboto patikra",
    BlockKind.LOGIN: "Reikia prisijungti",
    BlockKind.ROBOTS: "Draudžia robots.txt",
    BlockKind.FORBIDDEN: "Draudžiama (403)",
    BlockKind.RATE_LIMITED: "Per dažnai (429)",
}

# When a crawl records several kinds, the run shows the first of these: the one a human
# can act on most directly.
PRIORITY: tuple[BlockKind, ...] = (
    BlockKind.CAPTCHA,
    BlockKind.LOGIN,
    BlockKind.CHALLENGE,
    BlockKind.ROBOTS,
    BlockKind.FORBIDDEN,
    BlockKind.RATE_LIMITED,
)

# Kinds that do not clear by waiting: a person has to solve, log in or decide.
# 403 and 429 can be transient, so they block a run without asking for a human.
NEEDS_HUMAN: frozenset[BlockKind] = frozenset(
    {BlockKind.CAPTCHA, BlockKind.CHALLENGE, BlockKind.LOGIN, BlockKind.ROBOTS}
)

# HTTP answers that mean "not for you", and the kind each implies when no block page says
# more. The middleware counts them as they arrive; summarize_stats also reads Scrapy's own
# status counts, so stats from a crawl without the middleware still name the block.
STATUS_KINDS: dict[int, BlockKind] = {
    401: BlockKind.LOGIN,
    403: BlockKind.FORBIDDEN,
    429: BlockKind.RATE_LIMITED,
}

# Crawl close reasons that are a block, not a crash. Scrapy's RobotsTxtMiddleware closes
# the spider with "robotstxt_denied" when robots.txt forbade every start request.
FINISH_REASON_KINDS: dict[str, BlockKind] = {"robotstxt_denied": BlockKind.ROBOTS}

STAT_PREFIX = "nt/block/"
# A spider may set this stat itself (e.g. a human-assisted session waiting, #15).
NEEDS_HUMAN_STAT = "nt/needs_human"


def stat_key(kind: BlockKind) -> str:
    """Scrapy stats key counting responses of this kind in one crawl."""
    return f"{STAT_PREFIX}{kind.value}"
