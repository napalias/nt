from django.conf import settings
from django.test import Client

from apps.search.models import SavedSearch


def test_search_defaults_expose_the_configured_radius():
    resp = Client().get("/api/search/defaults")
    assert resp.status_code == 200
    assert resp.json() == {"radius_m": settings.DEFAULT_SEARCH_RADIUS_M}


def test_saved_search_radius_default_follows_the_setting():
    field = SavedSearch._meta.get_field("radius_m")
    assert field.default == settings.DEFAULT_SEARCH_RADIUS_M
