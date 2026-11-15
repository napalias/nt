"""Base settings shared across all environments."""

from pathlib import Path

import environ

# backend/config/settings/base.py → backend/
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()

# In Docker, env vars come from docker-compose env_file. Locally, read .env from repo root.
_env_file = BASE_DIR.parent / ".env"
if _env_file.is_file():
    env.read_env(_env_file, overwrite=False)

# --- Security ---
SECRET_KEY = env("SECRET_KEY")

# --- Applications ---
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    # Third-party
    "django_celery_beat",
    "django_extensions",
    "ninja",
    # Local apps
    "apps.listings",
    "apps.geo",
    "apps.search",
    "apps.classifier",
    "apps.developers",
    "apps.cadastre",
    "apps.permits",
    "apps.planning",
    "apps.documents",
]

# --- Middleware ---
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# --- Templates ---
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Database ---
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["ENGINE"] = "django.contrib.gis.db.backends.postgis"

# --- Auth password validators ---
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Internationalization ---
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Vilnius"
USE_I18N = True
USE_TZ = True

# --- Static files ---
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- Default primary key ---
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Cache ---
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# --- Celery ---
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://redis:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
# run_spider shells out to `scrapy`, which only exists in the scrapers image.
# It goes to the `scraping` queue, consumed by the `scraper_worker` compose service.
CELERY_TASK_ROUTES = {
    "apps.listings.tasks.run_spider": {"queue": "scraping"},
}

# --- App-specific ---
DEFAULT_SEARCH_RADIUS_M = 5000
# Which listing targets of the source registry (apps/listings/sources.py) scheduled scans
# run. Listing.PropertyType values, comma-separated in the env. Scope: houses for sale.
SCAN_PROPERTY_TYPES = env.list("SCAN_PROPERTY_TYPES", default=["house"])
# Saved-search digest recipient. Empty means digest emails are skipped.
NOTIFY_EMAIL = env("NOTIFY_EMAIL", default="")
NOMINATIM_URL = env("NOMINATIM_URL", default="http://nominatim:8080")

# --- AI Classification ---
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")
# Narrative evaluation and planning-document extraction.
CLAUDE_EVAL_MODEL = env("CLAUDE_EVAL_MODEL", default="claude-sonnet-5-5")
# Cleanup, preference extraction and the dedup fallback.
CLAUDE_FAST_MODEL = env("CLAUDE_FAST_MODEL", default="claude-haiku-5-5")

# --- Jev (TypeSafe System One) judgment layer: apps/classifier/judge.py ---
# off: today's path, Jev is never called. shadow: the current decider decides, Jev is asked
# too and both are logged. on: Jev decides when its probability clears the question's
# threshold, otherwise the fallback decides. TYPESAFE_API_KEY is read from os.environ at
# call time, never from settings.
JEV_MODE = env("JEV_MODE", default="off")
# A versioned ID. Aliases (jev-latest, jev-preview) move between releases and shift
# confidence, so judge.py refuses them.
JEV_MODEL = env("JEV_MODEL", default="jev-1.14.0")
# Per-HTTP-operation timeout, and retries after the first attempt. The worst case for one
# request is about (1 + retries) x timeout plus a short backoff, then the fallback decides.
JEV_TIMEOUT_S = env.float("JEV_TIMEOUT_S", default=10.0)
JEV_MAX_RETRIES = env.int("JEV_MAX_RETRIES", default=1)
# Yes/no questions: Jev decides "yes" at P(yes) >= yes and "no" at P(yes) <= no.
# Choice and score questions: Jev decides when its top option has P >= min_probability.
JEV_DEFAULT_THRESHOLD = {"yes": 0.90, "no": 0.10, "min_probability": 0.90}
# Per-question overrides keyed by question name, e.g. {"same_property": {"yes": 0.95}}.
# Missing keys inherit JEV_DEFAULT_THRESHOLD.
JEV_THRESHOLDS: dict[str, dict[str, float]] = {}

# --- Rate limiting ---
RATELIMIT_USE_CACHE = "default"
RATELIMIT_VIEW_RATE = "60/m"  # default fallback rate

# --- GeoPortal WFS ---
GEOPORTAL_WFS_URL = env("GEOPORTAL_WFS_URL", default="https://www.geoportal.lt/mapproxy/wfs")
GEOPORTAL_LAYER_CADASTRE = env("GEOPORTAL_LAYER_CADASTRE", default="geoportal:NTKR_SKLYPAI")
GEOPORTAL_LAYER_HERITAGE = env("GEOPORTAL_LAYER_HERITAGE", default="geoportal:KVR_OBJEKTAI")
GEOPORTAL_LAYER_RESTRICTIONS = env("GEOPORTAL_LAYER_RESTRICTIONS", default="geoportal:SZNS_ZONOS")
