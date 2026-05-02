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

# --- App-specific ---
DEFAULT_SEARCH_RADIUS_M = 5000
NOMINATIM_URL = env("NOMINATIM_URL", default="http://nominatim:8080")

# --- AI Classification ---
ANTHROPIC_API_KEY = env("ANTHROPIC_API_KEY", default="")

# --- Rate limiting ---
RATELIMIT_USE_CACHE = "default"
RATELIMIT_VIEW_RATE = "60/m"  # default fallback rate

# --- GeoPortal WFS ---
GEOPORTAL_WFS_URL = env("GEOPORTAL_WFS_URL", default="https://www.geoportal.lt/mapproxy/wfs")
GEOPORTAL_LAYER_CADASTRE = env("GEOPORTAL_LAYER_CADASTRE", default="geoportal:NTKR_SKLYPAI")
GEOPORTAL_LAYER_HERITAGE = env("GEOPORTAL_LAYER_HERITAGE", default="geoportal:KVR_OBJEKTAI")
GEOPORTAL_LAYER_RESTRICTIONS = env("GEOPORTAL_LAYER_RESTRICTIONS", default="geoportal:SZNS_ZONOS")
