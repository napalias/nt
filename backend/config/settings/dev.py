"""Development settings."""

from .base import *  # noqa: F403, F401

DEBUG = True
ALLOWED_HOSTS = ["*"]

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "mailhog"
EMAIL_PORT = 1025

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}
RATELIMIT_ENABLE = False
