"""Why a site stopped answering us: the block kinds a crawl can record.

Plain Python on purpose (no Django import), like sources.py: the scrapers' block-detection
downloader middleware imports it to name its Scrapy stats, and the backend reads the same
stats back into ScrapeRun.block_kind (apps/listings/scrape_runs.py).
"""

from __future__ import annotations

import re
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


# --- Telling a block page from the page we asked for -------------------------------------
# Shared by the scrapers' BlockDetectionMiddleware and the validity checker
# (apps/listings/tasks.py), so both call the same responses blocked.

# Challenge pages are small; a real page's markers sit near the top. Bound the work.
MAX_SCAN_CHARS = 300_000

# Each marker appears only on the block page itself, never on a normal page of the site.
# Deliberately absent: "g-recaptcha" (contact forms), "_pxAppId" and Cloudflare's
# "/cdn-cgi/challenge-platform/" script (both injected into every normal page).
CAPTCHA_MARKERS = (
    "px-captcha",  # PerimeterX (aruodas)
    "access to this page has been denied",  # PerimeterX block page title
    "captcha-delivery.com",  # DataDome
)
CHALLENGE_MARKERS = (
    "_cf_chl_opt",  # Cloudflare challenge bootstrap
    "cf-browser-verification",  # Cloudflare, older challenge page
    "checking your browser before accessing",
    "incapsula incident id",  # Imperva block page
)
CHALLENGE_TITLES = ("just a moment", "attention required! | cloudflare")
CAPTCHA_TITLES = ("captcha", "are you a robot", "are you human", "nesate robotas")

# "Prisijungimas" / "prisijungti" = log in (Lithuanian).
LOGIN_TITLE_RE = re.compile(r"prisijung|\blog ?in\b|\bsign ?in\b", re.IGNORECASE)
PASSWORD_INPUT_RE = re.compile(r"<input[^>]+type\s*=\s*[\"']?password", re.IGNORECASE)
LOGIN_PATH_RE = re.compile(
    r"(?:^|/)(?:login|log-in|signin|sign-in|prisijung[a-z]*)(?:[/?#.]|$)", re.IGNORECASE
)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _path_and_query(url: str) -> str:
    rest = url.split("://", 1)[-1]
    slash = rest.find("/")
    return rest[slash:] if slash >= 0 else "/"


def classify_response(
    status: int, url: str, text: str | None, redirected: bool = False
) -> BlockKind | None:
    """Why this response is not the page we asked for, or None if it is.

    Body markers win over the status code, so a PerimeterX page counts as a captcha whether
    it came with 200 or 403. A login page is one titled "log in" that has a password field
    (a header login modal on a normal page has a normal title), or one the site redirected
    us to. `text` is None for non-text responses: only the status speaks then.
    """
    body = (text or "")[:MAX_SCAN_CHARS].lower()
    title_match = TITLE_RE.search(body)
    title = title_match.group(1).strip() if title_match else ""

    if any(m in body for m in CAPTCHA_MARKERS) or any(t in title for t in CAPTCHA_TITLES):
        return BlockKind.CAPTCHA
    if any(m in body for m in CHALLENGE_MARKERS) or title.startswith(CHALLENGE_TITLES):
        return BlockKind.CHALLENGE
    if LOGIN_TITLE_RE.search(title) and PASSWORD_INPUT_RE.search(body):
        return BlockKind.LOGIN
    if redirected and LOGIN_PATH_RE.search(_path_and_query(url)):
        return BlockKind.LOGIN
    return STATUS_KINDS.get(status)
