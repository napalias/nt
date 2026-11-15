"""Shared fakes for the Anthropic client. No network, no API key."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock


def text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def thinking_block() -> SimpleNamespace:
    # SimpleNamespace has no .text, so code that reads content[0].text or touches
    # .text on a non-text block fails loudly in tests.
    return SimpleNamespace(type="thinking", thinking="")


def make_response(
    payload: dict | None = None,
    *,
    text: str | None = None,
    stop_reason: str = "end_turn",
    blocks: list | None = None,
) -> SimpleNamespace:
    if blocks is None:
        body = text if text is not None else json.dumps(payload)
        blocks = [thinking_block(), text_block(body)]
    return SimpleNamespace(content=blocks, stop_reason=stop_reason, usage=None)


def make_client(*responses) -> MagicMock:
    """A fake client whose messages.create returns/raises the given items in turn."""
    client = MagicMock()
    if len(responses) == 1:
        client.messages.create.return_value = responses[0]
    else:
        client.messages.create.side_effect = list(responses)
    return client


CLASSIFY_PAYLOAD = {
    "verdict": "match",
    "match_score": 0.85,
    "summary": "Puikus namas Kretingoje, atitinka visus kriterijus.",
    "hard_filter_results": {
        "price_ok": True,
        "type_ok": True,
        "area_ok": True,
        "plot_ok": True,
        "location_ok": True,
    },
    "quality_notes": ["Good photos", "Complete description"],
    "red_flags": [],
}

PREFERENCES_PAYLOAD = {
    "patterns": [
        {"pattern": "Prefers open-plan layouts", "weight": 1.2},
        {"pattern": "Values large windows facing south", "weight": 1.0},
    ]
}

COMPARE_PAYLOAD = {"is_same": True, "confidence": 0.9, "reasoning": "Same address and area."}

PLANNING_PAYLOAD = {
    "allowed_uses": ["residential"],
    "max_height_m": 9.5,
    "max_floors": 2,
    "max_density": None,
    "parking_requirements": "",
    "extraction_confidence": 0.8,
    "key_restrictions": ["No fences above 1.5 m"],
}
