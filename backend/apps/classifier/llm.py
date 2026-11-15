"""The one place that talks to the Anthropic Messages API.

Everything that calls Claude (evaluation, cleanup, preference extraction, dedup fallback,
planning extraction) goes through here so the safety rules live in one spot:

- Models come from settings (``CLAUDE_EVAL_MODEL`` / ``CLAUDE_FAST_MODEL``), never literals.
- Structured output uses ``output_config.format`` (JSON schema). No forced ``tool_choice``:
  Sonnet 5.5 rejects it with a 400.
- The reply text is read by block type. Adaptive thinking puts a ``thinking`` block first,
  so ``content[0].text`` is wrong.
- ``stop_reason`` is checked before the text is trusted. A ``max_tokens`` stop means the
  output is cut off, so it is raised, never saved.
- ``max_tokens`` here has to cover thinking tokens too, so callers pass generous values.

The API key is read from ``settings.ANTHROPIC_API_KEY`` (which comes from the environment)
and is never logged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import anthropic
from django.conf import settings

logger = logging.getLogger(__name__)

# Effort levels for output_config. Evaluation narrative wants some thought; the fast
# model does mechanical extraction and dedup comparisons.
EFFORT_EVAL = "medium"
EFFORT_FAST = "low"

_OK_STOP_REASONS = {"end_turn", "stop_sequence"}


class LLMOutputError(Exception):
    """The model answered, but the answer cannot be used."""


class TruncatedOutputError(LLMOutputError):
    """stop_reason was max_tokens: the output is cut off."""


class RefusalError(LLMOutputError):
    """stop_reason was refusal."""


class UnexpectedStopReasonError(LLMOutputError):
    """stop_reason was something a plain one-shot call should never end on."""


class EmptyOutputError(LLMOutputError):
    """No text came back."""


class InvalidOutputError(LLMOutputError):
    """Text came back but is not the JSON object (or values) we asked for."""


def get_client() -> anthropic.Anthropic:
    api_key = settings.ANTHROPIC_API_KEY
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env file to use AI classification."
        )
    return anthropic.Anthropic(api_key=api_key)


def read_text(response: Any, purpose: str) -> str:
    """Return the reply text after checking stop_reason. Raises LLMOutputError subclasses."""
    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "max_tokens":
        raise TruncatedOutputError(f"{purpose}: output hit max_tokens and was cut off")
    if stop_reason == "refusal":
        raise RefusalError(f"{purpose}: the model refused the request")
    if stop_reason not in _OK_STOP_REASONS:
        raise UnexpectedStopReasonError(f"{purpose}: unexpected stop_reason {stop_reason!r}")

    text = "".join(block.text for block in response.content if block.type == "text")
    if not text.strip():
        raise EmptyOutputError(f"{purpose}: response had no text block")
    return text


def call_structured(
    client: anthropic.Anthropic,
    *,
    model: str,
    max_tokens: int,
    messages: list[dict],
    schema: dict,
    purpose: str,
    system: Any = None,
    effort: str = EFFORT_FAST,
) -> dict:
    """One Messages call that must return a JSON object matching ``schema``.

    The schema is enforced by the API (structured outputs), so keep it to the supported
    subset: ``additionalProperties: false`` on objects, no numeric min/max. Range checks
    belong in the caller (see ``require_unit_interval``).
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "output_config": {
            "effort": effort,
            "format": {"type": "json_schema", "schema": schema},
        },
    }
    if system is not None:
        kwargs["system"] = system

    response = client.messages.create(**kwargs)
    text = read_text(response, purpose)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidOutputError(f"{purpose}: response is not valid JSON") from exc
    if not isinstance(data, dict):
        raise InvalidOutputError(f"{purpose}: expected a JSON object, got {type(data).__name__}")
    return data


def call_text(
    client: anthropic.Anthropic,
    *,
    model: str,
    max_tokens: int,
    messages: list[dict],
    purpose: str,
    system: Any = None,
    effort: str = EFFORT_FAST,
) -> str:
    """One Messages call that returns free text, with the same stop_reason guarantees."""
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "output_config": {"effort": effort},
    }
    if system is not None:
        kwargs["system"] = system

    response = client.messages.create(**kwargs)
    return read_text(response, purpose).strip()


def require_unit_interval(value: Any, name: str) -> float:
    """Validate a 0..1 number. Structured outputs cannot enforce minimum/maximum."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InvalidOutputError(f"{name} must be a number, got {value!r}")
    if not 0.0 <= value <= 1.0:
        raise InvalidOutputError(f"{name} must be between 0 and 1, got {value!r}")
    return float(value)


def require_keys(data: dict, keys: tuple[str, ...], purpose: str) -> None:
    missing = [k for k in keys if k not in data]
    if missing:
        raise InvalidOutputError(f"{purpose}: response is missing {', '.join(missing)}")


@dataclass
class BatchResult:
    """Outcome of a batch run: what worked, how many failed, and why."""

    items: list = field(default_factory=list)
    failed: int = 0
    error_counts: dict[str, int] = field(default_factory=dict)
    _logged_types: set[str] = field(default_factory=set, repr=False)

    def add_failure(self, exc: BaseException, context: str = "") -> None:
        name = type(exc).__name__
        self.failed += 1
        self.error_counts[name] = self.error_counts.get(name, 0) + 1
        # Full traceback once per error type; one-liners after that so a dead model
        # does not write 50 identical tracebacks.
        if name not in self._logged_types:
            self._logged_types.add(name)
            logger.error("%s failed: %s: %s", context or "item", name, exc, exc_info=exc)
        else:
            logger.warning("%s failed: %s: %s", context or "item", name, exc)

    def log_summary(self, label: str) -> None:
        ok = len(self.items)
        if self.failed:
            logger.error(
                "%s: %d succeeded, %d failed (%s)",
                label,
                ok,
                self.failed,
                self.error_counts,
            )
        else:
            logger.info("%s: %d succeeded, 0 failed", label, ok)
