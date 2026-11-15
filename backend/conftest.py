import pytest
from django.contrib.gis.geos import Point


@pytest.fixture
def kretinga_point():
    return Point(21.2420, 55.8835, srid=4326)


@pytest.fixture
def vilnius_point():
    return Point(25.2797, 54.6872, srid=4326)


@pytest.fixture(autouse=True)
def no_live_jev(monkeypatch, settings):
    """Tests never reach TypeSafe: no key, no cached client, Jev off unless a test opts in.

    The containers carry a real TYPESAFE_API_KEY, so without this a test that forgets to pass
    a fake client would make a paid network call.
    """
    from apps.classifier import judge

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(judge, "_default_client", None)
    settings.JEV_MODE = "off"


@pytest.fixture(autouse=True)
def notify_recipient(settings):
    """Digest emails need a recipient. Tests use a placeholder, never a real address."""
    settings.NOTIFY_EMAIL = "digest@example.test"
