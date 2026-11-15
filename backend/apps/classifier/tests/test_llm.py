import json
import os

import pytest

from apps.classifier import llm
from apps.classifier.tests.helpers import (
    make_client,
    make_response,
    text_block,
    thinking_block,
)

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def _call(client, **overrides):
    kwargs = {
        "model": "claude-sonnet-5-5",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": "hi"}],
        "schema": SCHEMA,
        "purpose": "test",
    }
    kwargs.update(overrides)
    return llm.call_structured(client, **kwargs)


def _call_text(client, **overrides):
    kwargs = {
        "model": "claude-haiku-5-5",
        "max_tokens": 500,
        "system": "s",
        "messages": [{"role": "user", "content": "x"}],
        "purpose": "test",
    }
    kwargs.update(overrides)
    return llm.call_text(client, **kwargs)


class TestSettings:
    def test_model_settings_exist(self, settings):
        assert settings.CLAUDE_EVAL_MODEL
        assert settings.CLAUDE_FAST_MODEL

    @pytest.mark.skipif(
        "CLAUDE_EVAL_MODEL" in os.environ or "CLAUDE_FAST_MODEL" in os.environ,
        reason="env overrides the defaults",
    )
    def test_defaults(self, settings):
        assert settings.CLAUDE_EVAL_MODEL == "claude-sonnet-5-5"
        assert settings.CLAUDE_FAST_MODEL == "claude-haiku-5-5"


class TestGetClient:
    def test_missing_key_raises_clear_error(self, settings):
        settings.ANTHROPIC_API_KEY = ""
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            llm.get_client()


class TestCallStructured:
    def test_request_uses_structured_outputs_and_no_forced_tool_choice(self):
        client = make_client(make_response({"ok": True}))
        _call(client, system="sys", effort="low")
        kwargs = client.messages.create.call_args.kwargs
        assert "tool_choice" not in kwargs
        assert "tools" not in kwargs
        assert kwargs["model"] == "claude-sonnet-5-5"
        assert kwargs["max_tokens"] == 1000
        assert kwargs["system"] == "sys"
        fmt = kwargs["output_config"]["format"]
        assert fmt == {"type": "json_schema", "schema": SCHEMA}
        assert kwargs["output_config"]["effort"] == "low"

    def test_reads_text_block_by_type_not_position(self):
        # A thinking block comes first; content[0].text would blow up.
        resp = make_response(blocks=[thinking_block(), text_block(json.dumps({"ok": True}))])
        assert _call(make_client(resp)) == {"ok": True}

    def test_joins_multiple_text_blocks(self):
        resp = make_response(blocks=[text_block('{"ok":'), text_block(" true}")])
        assert _call(make_client(resp)) == {"ok": True}

    def test_max_tokens_stop_reason_raises_truncated(self):
        # The body here is complete valid JSON; the stop reason alone must reject it.
        resp = make_response({"ok": True}, stop_reason="max_tokens")
        with pytest.raises(llm.TruncatedOutputError):
            _call(make_client(resp))

    def test_refusal_raises(self):
        resp = make_response(blocks=[], stop_reason="refusal")
        with pytest.raises(llm.RefusalError):
            _call(make_client(resp))

    def test_unexpected_stop_reason_raises(self):
        resp = make_response({"ok": True}, stop_reason="pause_turn")
        with pytest.raises(llm.UnexpectedStopReasonError):
            _call(make_client(resp))

    def test_no_text_block_raises_empty(self):
        resp = make_response(blocks=[thinking_block()])
        with pytest.raises(llm.EmptyOutputError):
            _call(make_client(resp))

    def test_invalid_json_raises_invalid_output(self):
        resp = make_response(text="not json")
        with pytest.raises(llm.InvalidOutputError):
            _call(make_client(resp))

    def test_non_object_json_raises_invalid_output(self):
        resp = make_response(text="[1, 2]")
        with pytest.raises(llm.InvalidOutputError):
            _call(make_client(resp))


class TestCallText:
    def test_returns_stripped_text(self):
        client = make_client(make_response(text="  hello  "))
        assert _call_text(client) == "hello"
        kwargs = client.messages.create.call_args.kwargs
        assert "tool_choice" not in kwargs
        assert "format" not in kwargs["output_config"]

    def test_truncated_text_raises(self):
        resp = make_response(text="half a sent", stop_reason="max_tokens")
        with pytest.raises(llm.TruncatedOutputError):
            _call_text(make_client(resp))

    def test_blank_text_raises_empty(self):
        with pytest.raises(llm.EmptyOutputError):
            _call_text(make_client(make_response(text="   ")))


class TestRequireUnitInterval:
    def test_accepts_bounds(self):
        assert llm.require_unit_interval(0, "x") == 0.0
        assert llm.require_unit_interval(1, "x") == 1.0

    @pytest.mark.parametrize("bad", [-0.1, 1.1, "0.5", None, True])
    def test_rejects(self, bad):
        with pytest.raises(llm.InvalidOutputError):
            llm.require_unit_interval(bad, "x")


class TestBatchResult:
    def test_records_failures_by_type(self):
        result = llm.BatchResult()
        result.add_failure(llm.TruncatedOutputError("a"))
        result.add_failure(llm.TruncatedOutputError("b"))
        result.add_failure(ValueError("c"))
        assert result.failed == 3
        assert result.error_counts == {"TruncatedOutputError": 2, "ValueError": 1}

    def test_log_summary_is_error_level_when_failures(self, caplog):
        result = llm.BatchResult()
        result.add_failure(ValueError("x"))
        with caplog.at_level("ERROR", logger="apps.classifier.llm"):
            result.log_summary("classify")
        assert any(
            r.levelname == "ERROR" and "ValueError" in r.getMessage() for r in caplog.records
        )

    def test_log_summary_info_when_clean(self, caplog):
        result = llm.BatchResult(items=[1])
        with caplog.at_level("INFO", logger="apps.classifier.llm"):
            result.log_summary("classify")
        assert not any(r.levelname == "ERROR" for r in caplog.records)
