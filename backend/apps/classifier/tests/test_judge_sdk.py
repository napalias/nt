"""The real SDK adapter against a mocked HTTP transport. No network, no key.

Skipped where typesafe-sdk is not installed (an image built before it was added).
"""

from __future__ import annotations

import json

import pytest

typesafe_sdk = pytest.importorskip("typesafe_sdk")
httpx2 = pytest.importorskip("httpx2")

from apps.classifier import judge  # noqa: E402
from apps.classifier.judge import Question  # noqa: E402

HOUSE = Question.yes_no("is_house_ad", "Is this an ad selling a whole house?")
WALLS = Question.choice("wall_kind", "What are the walls made of?", {"brick": None, "wood": None})


def _client(handler) -> judge.SdkJevClient:
    return judge.SdkJevClient(
        api_key="test-key-not-real", max_retries=0, transport=httpx2.MockTransport(handler)
    )


def test_request_body_and_parsed_response():
    seen: dict = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth_scheme"] = request.headers["authorization"].split(" ")[0]
        seen["body"] = json.loads(request.content)
        return httpx2.Response(
            200,
            json={
                "model": "jev-1.14.0",
                "answers": {
                    "is_house_ad": {"type": "noul", "noul": 0.96},
                    "wall_kind": {
                        "type": "choice",
                        "choice": "wood",
                        "confidence": 0.8,
                        "probabilities": {"brick": 0.08, "wood": 0.92},
                    },
                },
                "usage": {"input_tokens": 150, "output_tokens": 7},
            },
        )

    raw = _client(handler).system_one(
        state={"title": "Namas"},
        questions={q.name: q.to_request() for q in (HOUSE, WALLS)},
        model="jev-1.14.0",
        timeout=5.0,
    )

    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["auth_scheme"] == "Bearer"
    assert seen["body"]["model"] == "jev-1.14.0"
    assert seen["body"]["state"] == {"title": "Namas"}
    assert seen["body"]["questions"]["is_house_ad"] == {
        "type": "noul",
        "instructions": "Is this an ad selling a whole house?",
    }
    assert raw["model"] == "jev-1.14.0"
    assert raw["usage"] == {"input_tokens": 150, "output_tokens": 7}
    answer = judge.parse_answer(WALLS, raw["answers"]["wall_kind"], judge.threshold_for("w"))
    assert (answer.value, answer.probability, answer.decisive) == ("wood", 0.92, True)


@pytest.mark.django_db
def test_server_error_becomes_a_fallback(settings):
    settings.JEV_MODE = "on"
    settings.JEV_MODEL = "jev-1.14.0"

    def handler(request):
        return httpx2.Response(529, json={"detail": "overloaded"})

    result = judge.ask(
        "x", 1, {}, [HOUSE], lambda qs: {"is_house_ad": True}, client=_client(handler)
    )
    assert result["is_house_ad"].decided_by == "fallback"
    assert "529" in result["is_house_ad"].jev_error


@pytest.mark.django_db
def test_timeout_becomes_a_fallback(settings):
    settings.JEV_MODE = "on"
    settings.JEV_MODEL = "jev-1.14.0"

    def handler(request):
        raise httpx2.ReadTimeout("slow", request=request)

    result = judge.ask(
        "x", 1, {}, [HOUSE], lambda qs: {"is_house_ad": True}, client=_client(handler)
    )
    assert result["is_house_ad"].decided_by == "fallback"
    assert "TypeSafeAPITimeoutError" in result["is_house_ad"].jev_error


def test_error_text_never_contains_the_key():
    def handler(request):
        return httpx2.Response(401, json={"detail": "bad key"})

    with pytest.raises(typesafe_sdk.TypeSafeError) as excinfo:
        _client(handler).system_one(
            state="x", questions={"q": HOUSE.to_request()}, model="jev-1.14.0", timeout=5.0
        )
    assert "test-key-not-real" not in str(excinfo.value)
    assert "test-key-not-real" not in repr(excinfo.value)
