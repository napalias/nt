from unittest.mock import patch

import pytest

from apps.classifier import llm
from apps.classifier.tests.helpers import (
    PLANNING_PAYLOAD,
    make_client,
    make_response,
)
from apps.planning.services.extract import extract_facts_from_text, extract_planning_facts


class FakeDocs:
    def __init__(self, texts):
        self._docs = [FakeDoc(t) for t in texts]

    def all(self):
        return self._docs


class FakeDoc:
    def __init__(self, text):
        self.extracted_text = text
        self.storage_path = ""

    def save(self, **kwargs):  # pragma: no cover - never reached with extracted_text set
        raise AssertionError("should not save")


class FakePlanningDoc:
    pk = 1

    def __init__(self, texts):
        self.documents = FakeDocs(texts)
        self.saved_fields = None

    def save(self, update_fields=None):
        self.saved_fields = update_fields


class TestExtractFactsFromText:
    @patch("apps.planning.services.extract.get_client")
    def test_uses_eval_model_and_structured_output(self, mock_get_client, settings):
        settings.CLAUDE_EVAL_MODEL = "claude-test-eval"
        client = make_client(make_response(PLANNING_PAYLOAD))
        mock_get_client.return_value = client

        facts = extract_facts_from_text("Maksimalus aukštis 9,5 m")

        assert facts["max_height_m"] == 9.5
        assert facts["max_density"] is None
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["model"] == "claude-test-eval"
        assert "tool_choice" not in kwargs
        assert "tools" not in kwargs
        schema = kwargs["output_config"]["format"]["schema"]
        assert schema["additionalProperties"] is False
        assert "extraction_confidence" in schema["required"]

    @patch("apps.planning.services.extract.get_client")
    def test_truncated_response_raises(self, mock_get_client):
        mock_get_client.return_value = make_client(
            make_response(PLANNING_PAYLOAD, stop_reason="max_tokens")
        )
        with pytest.raises(llm.TruncatedOutputError):
            extract_facts_from_text("text")

    @patch("apps.planning.services.extract.get_client")
    def test_out_of_range_confidence_raises(self, mock_get_client):
        mock_get_client.return_value = make_client(
            make_response({**PLANNING_PAYLOAD, "extraction_confidence": 80})
        )
        with pytest.raises(llm.InvalidOutputError):
            extract_facts_from_text("text")

    @patch("apps.planning.services.extract.get_client")
    def test_missing_required_field_raises(self, mock_get_client):
        bad = {k: v for k, v in PLANNING_PAYLOAD.items() if k != "allowed_uses"}
        mock_get_client.return_value = make_client(make_response(bad))
        with pytest.raises(llm.InvalidOutputError):
            extract_facts_from_text("text")

    def test_missing_api_key_fails_loudly(self, settings):
        settings.ANTHROPIC_API_KEY = ""
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            extract_facts_from_text("text")


class TestExtractPlanningFacts:
    @patch("apps.planning.services.extract.get_client")
    def test_saves_facts(self, mock_get_client):
        mock_get_client.return_value = make_client(make_response(PLANNING_PAYLOAD))
        doc = FakePlanningDoc(["some plan text"])

        facts = extract_planning_facts(doc)

        assert facts["allowed_uses"] == ["residential"]
        assert doc.max_floors == 2
        assert doc.extraction_confidence == 0.8
        assert doc.saved_fields is not None

    @patch("apps.planning.services.extract.get_client")
    def test_truncated_output_is_logged_and_not_saved(self, mock_get_client, caplog):
        mock_get_client.return_value = make_client(
            make_response(PLANNING_PAYLOAD, stop_reason="max_tokens")
        )
        doc = FakePlanningDoc(["some plan text"])

        with caplog.at_level("ERROR"):
            facts = extract_planning_facts(doc)

        assert facts is None
        assert doc.saved_fields is None
        assert any(
            r.levelname == "ERROR" and "TruncatedOutputError" in r.getMessage()
            for r in caplog.records
        )
