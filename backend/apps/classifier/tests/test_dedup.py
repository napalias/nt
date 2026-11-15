from unittest.mock import MagicMock, patch

import pytest
from django.contrib.gis.geos import Point
from model_bakery import baker

from apps.classifier import llm
from apps.classifier.dedup import compare_pair, run_dedup
from apps.classifier.models import ListingCluster
from apps.classifier.tests.helpers import COMPARE_PAYLOAD, make_client, make_response
from apps.listings.models import Listing

POINT = Point(21.2420, 55.8835, srid=4326)


def _pair():
    a = baker.make(Listing, source="domoplius", location=POINT, address_raw="A", rooms=4)
    b = baker.make(Listing, source="skelbiu", location=POINT, address_raw="A", rooms=4)
    return a, b


@pytest.mark.django_db
class TestComparePair:
    @patch("apps.classifier.dedup.get_client")
    def test_uses_fast_model_structured_output(self, mock_get_client, settings):
        settings.CLAUDE_FAST_MODEL = "claude-test-fast"
        client = make_client(make_response(COMPARE_PAYLOAD))
        mock_get_client.return_value = client
        a, b = _pair()

        result = compare_pair(a, b)

        assert result["is_same"] is True
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-test-fast"
        assert "tool_choice" not in kwargs
        assert "tools" not in kwargs
        assert kwargs["output_config"]["format"]["schema"]["additionalProperties"] is False

    @patch("apps.classifier.dedup.get_client")
    def test_truncated_response_raises(self, mock_get_client):
        mock_get_client.return_value = make_client(
            make_response(COMPARE_PAYLOAD, stop_reason="max_tokens")
        )
        a, b = _pair()
        with pytest.raises(llm.TruncatedOutputError):
            compare_pair(a, b)

    @patch("apps.classifier.dedup.get_client")
    def test_out_of_range_confidence_raises(self, mock_get_client):
        mock_get_client.return_value = make_client(
            make_response({**COMPARE_PAYLOAD, "confidence": 4})
        )
        a, b = _pair()
        with pytest.raises(llm.InvalidOutputError):
            compare_pair(a, b)

    def test_missing_api_key_fails_loudly(self, settings):
        settings.ANTHROPIC_API_KEY = ""
        a, b = _pair()
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            compare_pair(a, b)


@pytest.mark.django_db
class TestRunDedup:
    @patch("apps.classifier.dedup.get_client")
    def test_creates_cluster_for_confirmed_match(self, mock_get_client):
        mock_get_client.return_value = make_client(make_response(COMPARE_PAYLOAD))
        _pair()
        clusters = run_dedup()
        assert len(clusters) == 1
        assert ListingCluster.objects.count() == 1

    @patch("apps.classifier.dedup.get_client")
    def test_comparison_failures_are_logged_as_error(self, mock_get_client, caplog):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("model not found")
        mock_get_client.return_value = client
        _pair()

        with caplog.at_level("ERROR"):
            clusters = run_dedup()

        assert clusters == []
        assert any(
            r.levelname == "ERROR"
            and "RuntimeError" in r.getMessage()
            and "dedup" in r.getMessage()
            for r in caplog.records
        )
