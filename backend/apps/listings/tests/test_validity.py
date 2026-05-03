from unittest.mock import MagicMock, patch

import pytest
from django.contrib.gis.geos import Point
from model_bakery import baker

from apps.listings.models import Listing
from apps.listings.tasks import check_listing_validity


def _make_listing(**kwargs) -> Listing:
    defaults = {
        "is_active": True,
        "location": Point(21.2420, 55.8835, srid=4326),
        "address_raw": "Kretinga",
        "source_url": "https://domoplius.lt/skelbimai/123",
    }
    defaults.update(kwargs)
    return baker.make(Listing, **defaults)


@pytest.mark.django_db
class TestCheckListingValidity:
    @patch("apps.listings.tasks.time.sleep")
    @patch("apps.listings.tasks.requests.get")
    def test_deactivates_on_404(self, mock_get, mock_sleep):
        listing = _make_listing()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        result = check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.is_active is False
        assert result["checked"] == 1
        assert result["deactivated"] == 1

    @patch("apps.listings.tasks.time.sleep")
    @patch("apps.listings.tasks.requests.get")
    def test_deactivates_on_410(self, mock_get, mock_sleep):
        listing = _make_listing()
        mock_resp = MagicMock()
        mock_resp.status_code = 410
        mock_get.return_value = mock_resp

        result = check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.is_active is False
        assert result["deactivated"] == 1

    @patch("apps.listings.tasks.time.sleep")
    @patch("apps.listings.tasks.requests.get")
    def test_deactivates_on_stale_marker_in_body(self, mock_get, mock_sleep):
        listing = _make_listing()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Šis skelbimas nebepasiekiamas</body></html>"
        mock_get.return_value = mock_resp

        result = check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.is_active is False
        assert result["deactivated"] == 1

    @patch("apps.listings.tasks.time.sleep")
    @patch("apps.listings.tasks.requests.get")
    def test_deactivates_on_skelbimas_nerastas(self, mock_get, mock_sleep):
        listing = _make_listing()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Skelbimas nerastas arba pašalintas</body></html>"
        mock_get.return_value = mock_resp

        result = check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.is_active is False
        assert result["deactivated"] == 1

    @patch("apps.listings.tasks.time.sleep")
    @patch("apps.listings.tasks.requests.get")
    def test_keeps_active_on_200_with_normal_body(self, mock_get, mock_sleep):
        listing = _make_listing()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Namas Kretingoje, 200000 EUR</body></html>"
        mock_get.return_value = mock_resp

        result = check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.is_active is True
        assert result["checked"] == 1
        assert result["deactivated"] == 0

    @patch("apps.listings.tasks.time.sleep")
    @patch("apps.listings.tasks.requests.get")
    def test_skips_on_network_error(self, mock_get, mock_sleep):
        import requests

        listing = _make_listing()
        mock_get.side_effect = requests.ConnectionError("Connection refused")

        result = check_listing_validity(batch_size=10)

        listing.refresh_from_db()
        assert listing.is_active is True
        assert result["checked"] == 1
        assert result["deactivated"] == 0

    @patch("apps.listings.tasks.time.sleep")
    @patch("apps.listings.tasks.requests.get")
    def test_respects_batch_size(self, mock_get, mock_sleep):
        for i in range(5):
            _make_listing(source_id=f"DOM-{i}")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html>OK</html>"
        mock_get.return_value = mock_resp

        result = check_listing_validity(batch_size=3)

        assert result["checked"] == 3
        assert mock_get.call_count == 3

    @patch("apps.listings.tasks.time.sleep")
    @patch("apps.listings.tasks.requests.get")
    def test_skips_inactive_listings(self, mock_get, mock_sleep):
        _make_listing(is_active=False)
        _make_listing(is_active=True)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html>OK</html>"
        mock_get.return_value = mock_resp

        result = check_listing_validity(batch_size=10)

        assert result["checked"] == 1

    @patch("apps.listings.tasks.time.sleep")
    @patch("apps.listings.tasks.requests.get")
    def test_mixed_results(self, mock_get, mock_sleep):
        active_listing = _make_listing(
            source_url="https://domoplius.lt/skelbimai/active",
        )
        stale_listing = _make_listing(
            source_url="https://domoplius.lt/skelbimai/stale",
        )
        gone_listing = _make_listing(
            source_url="https://domoplius.lt/skelbimai/gone",
        )

        def side_effect(url, **kwargs):
            resp = MagicMock()
            if "active" in url:
                resp.status_code = 200
                resp.text = "<html>Namas Kretingoje</html>"
            elif "stale" in url:
                resp.status_code = 200
                resp.text = "<html>Šis skelbimas nebepasiekiamas</html>"
            elif "gone" in url:
                resp.status_code = 404
            return resp

        mock_get.side_effect = side_effect

        result = check_listing_validity(batch_size=10)

        active_listing.refresh_from_db()
        stale_listing.refresh_from_db()
        gone_listing.refresh_from_db()

        assert active_listing.is_active is True
        assert stale_listing.is_active is False
        assert gone_listing.is_active is False
        assert result["checked"] == 3
        assert result["deactivated"] == 2


@pytest.mark.django_db
class TestCheckValidityAPI:
    @patch("apps.listings.tasks.time.sleep")
    @patch("apps.listings.tasks.requests.get")
    def test_check_validity_endpoint(self, mock_get, mock_sleep):
        from django.test import Client

        _make_listing()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html>OK</html>"
        mock_get.return_value = mock_resp

        client = Client()
        resp = client.post("/api/check-validity")
        assert resp.status_code == 200
        data = resp.json()
        assert "checked" in data
        assert "deactivated" in data
