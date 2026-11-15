import pytest
from django.contrib.gis.geos import Point


@pytest.fixture
def kretinga_point():
    return Point(21.2420, 55.8835, srid=4326)


@pytest.fixture
def vilnius_point():
    return Point(25.2797, 54.6872, srid=4326)


@pytest.fixture(autouse=True)
def notify_recipient(settings):
    """Digest emails need a recipient. Tests use a placeholder, never a real address."""
    settings.NOTIFY_EMAIL = "digest@example.test"
