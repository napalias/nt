"""Tests for the Jev judgment layer. A fake client stands in for TypeSafe: no network, no key."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ImproperlyConfigured
from model_bakery import baker

from apps.classifier import judge
from apps.classifier.judge import JevResponseError, Question
from apps.classifier.models import JudgmentLog

HOUSE = Question.yes_no("is_house_ad", "Is this an ad selling a whole house?")
WALLS = Question.choice(
    "wall_kind",
    "What are the walls made of?",
    {"brick": "Brick or block", "wood": "Timber or log", "unknown": "Not stated"},
)
STATE_OF_REPAIR = Question.score(
    "condition", "What condition is the house in?", ["needs rebuild", "needs repair", "ready"]
)


def noul(p: float) -> dict:
    return {"type": "noul", "noul": p}


def choice(picked: str, probabilities: dict[str, float], confidence: float = 0.8) -> dict:
    return {
        "type": "choice",
        "choice": picked,
        "confidence": confidence,
        "probabilities": probabilities,
    }


def score(probabilities: list[float]) -> dict:
    return {
        "type": "score",
        "score": sum(i * p for i, p in enumerate(probabilities)),
        "confidence": 0.5,
        "legend": {str(i): f"level {i}" for i in range(len(probabilities))},
        "probabilities": {str(i): p for i, p in enumerate(probabilities)},
    }


def envelope(answers: dict, model: str = "jev-1.14.0") -> dict:
    return {"model": model, "answers": answers, "usage": {"input_tokens": 321, "output_tokens": 9}}


class FakeJev:
    """Records each system_one call and returns (or raises) the configured outcome."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls: list[dict] = []

    def system_one(self, *, state, questions, model, timeout):
        self.calls.append(
            {"state": state, "questions": questions, "model": model, "timeout": timeout}
        )
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class RecordingFallback:
    def __init__(self, values: dict | None = None, error: Exception | None = None):
        self.values = values or {}
        self.error = error
        self.calls: list[list[str]] = []

    def __call__(self, questions):
        self.calls.append([q.name for q in questions])
        if self.error is not None:
            raise self.error
        return {q.name: self.values[q.name] for q in questions}


@pytest.fixture(autouse=True)
def jev_settings(settings):
    settings.JEV_MODEL = "jev-1.14.0"
    settings.JEV_TIMEOUT_S = 7.5
    settings.JEV_MAX_RETRIES = 0
    settings.JEV_DEFAULT_THRESHOLD = {"yes": 0.90, "no": 0.10, "min_probability": 0.90}
    settings.JEV_THRESHOLDS = {}
    return settings


# --- Questions -------------------------------------------------------------------------


class TestQuestion:
    def test_yes_no_request_shape(self):
        q = Question.yes_no("new_build", "Is it a new build?", yes="Built after 2020", no="Older")
        assert q.to_request() == {
            "type": "noul",
            "instructions": "Is it a new build?",
            "criteria": {"true": "Built after 2020", "false": "Older"},
        }

    def test_yes_no_without_criteria_omits_them(self):
        assert HOUSE.to_request() == {
            "type": "noul",
            "instructions": "Is this an ad selling a whole house?",
        }

    def test_choice_request_shape(self):
        assert WALLS.to_request() == {
            "type": "choice",
            "instructions": "What are the walls made of?",
            "criteria": {
                "brick": "Brick or block",
                "wood": "Timber or log",
                "unknown": "Not stated",
            },
        }

    def test_score_request_shape(self):
        assert STATE_OF_REPAIR.to_request()["criteria"] == [
            "needs rebuild",
            "needs repair",
            "ready",
        ]

    @pytest.mark.parametrize(
        "build",
        [
            lambda: Question.yes_no("", "x"),
            lambda: Question.yes_no("bad name!", "x"),
            lambda: Question.yes_no("ok", "  "),
            lambda: Question.choice("ok", "x", {}),
            lambda: Question.choice("ok", "x", {f"o{i}": None for i in range(256)}),
            lambda: Question.score("ok", "x", []),
            lambda: Question.score("ok", "x", [str(i) for i in range(11)]),
            lambda: Question("ok", "box", "x"),
        ],
    )
    def test_invalid_questions_are_rejected(self, build):
        with pytest.raises(ValueError):
            build()

    def test_check_value_accepts_the_right_types(self):
        assert HOUSE.check_value(True) is True
        assert WALLS.check_value("wood") == "wood"
        assert STATE_OF_REPAIR.check_value(2) == 2
        assert HOUSE.check_value(None) is None

    @pytest.mark.parametrize(
        ("question", "value"),
        [
            (HOUSE, 1),
            (HOUSE, "yes"),
            (WALLS, "steel"),
            (STATE_OF_REPAIR, 3),
            (STATE_OF_REPAIR, True),
        ],
    )
    def test_check_value_rejects_wrong_values(self, question, value):
        with pytest.raises(ValueError):
            question.check_value(value)


# --- Thresholds and mode -----------------------------------------------------------------


class TestSettings:
    def test_defaults_in_base_settings(self):
        from config.settings import base

        assert base.JEV_MODEL == "jev-1.14.0"
        assert base.JEV_DEFAULT_THRESHOLD["yes"] == 0.90
        assert base.JEV_DEFAULT_THRESHOLD["no"] == 0.10
        assert base.JEV_THRESHOLDS == {}

    def test_threshold_override_per_question(self, settings):
        settings.JEV_THRESHOLDS = {"is_house_ad": {"yes": 0.97}}
        t = judge.threshold_for("is_house_ad")
        assert (t.yes, t.no, t.min_probability) == (0.97, 0.10, 0.90)
        assert judge.threshold_for("other").yes == 0.90

    @pytest.mark.parametrize(
        "bad",
        [{"yes": 0.4}, {"no": 0.6}, {"yes": 1.2}, {"min_probability": 0.0}, {"maybe": 0.5}],
    )
    def test_invalid_thresholds_fail_loudly(self, settings, bad):
        settings.JEV_THRESHOLDS = {"q": bad}
        with pytest.raises(ImproperlyConfigured):
            judge.threshold_for("q")

    def test_unknown_mode_fails_loudly(self, settings):
        settings.JEV_MODE = "onn"
        with pytest.raises(ImproperlyConfigured):
            judge.current_mode()

    def test_mode_is_case_insensitive(self, settings):
        settings.JEV_MODE = " Shadow "
        assert judge.current_mode() == "shadow"


# --- Mode: off ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOffMode:
    def test_fallback_decides_and_jev_is_never_called(self, settings):
        settings.JEV_MODE = "off"
        client = FakeJev(envelope({"is_house_ad": noul(0.99)}))
        fallback = RecordingFallback({"is_house_ad": False})

        result = judge.ask("ingest", 7, {"title": "Namas"}, [HOUSE], fallback, client=client)

        assert client.calls == []
        assert result.mode == "off"
        assert result["is_house_ad"].value is False
        assert result["is_house_ad"].decided_by == "fallback"
        assert result["is_house_ad"].jev is None
        assert JudgmentLog.objects.count() == 0

    def test_without_fallback_nothing_decides(self, settings):
        settings.JEV_MODE = "off"
        result = judge.ask("ingest", 7, {"title": "Namas"}, [HOUSE], None, client=FakeJev(None))
        assert result["is_house_ad"].value is None
        assert result["is_house_ad"].decided_by == "none"

    def test_fallback_errors_propagate_as_today(self, settings):
        settings.JEV_MODE = "off"
        fallback = RecordingFallback(error=RuntimeError("claude down"))
        with pytest.raises(RuntimeError, match="claude down"):
            judge.ask("ingest", 7, {}, [HOUSE], fallback, client=FakeJev(None))


# --- Mode: shadow ------------------------------------------------------------------------


@pytest.mark.django_db
class TestShadowMode:
    def test_fallback_decides_jev_is_asked_and_both_are_logged(self, settings):
        settings.JEV_MODE = "shadow"
        client = FakeJev(
            envelope(
                {
                    "is_house_ad": noul(0.97),
                    "wall_kind": choice("wood", {"brick": 0.05, "wood": 0.93, "unknown": 0.02}),
                }
            )
        )
        fallback = RecordingFallback({"is_house_ad": True, "wall_kind": "brick"})

        result = judge.ask(
            "evaluation",
            42,
            {"title": "Medinis namas"},
            [HOUSE, WALLS],
            fallback,
            fallback_model="claude-haiku-5-5",
            client=client,
        )

        assert len(client.calls) == 1, "one request carries all questions"
        call = client.calls[0]
        assert call["model"] == "jev-1.14.0"
        assert call["timeout"] == 7.5
        assert set(call["questions"]) == {"is_house_ad", "wall_kind"}
        assert fallback.calls == [["is_house_ad", "wall_kind"]]

        assert result["is_house_ad"].value is True
        assert result["is_house_ad"].decided_by == "fallback"
        assert result["is_house_ad"].agreement is True
        assert result["wall_kind"].value == "brick"
        assert result["wall_kind"].jev.value == "wood"
        assert result["wall_kind"].agreement is False

        rows = {r.question: r for r in JudgmentLog.objects.all()}
        assert set(rows) == {"is_house_ad", "wall_kind"}
        house = rows["is_house_ad"]
        assert house.kind == "evaluation"
        assert house.subject_id == "42"
        assert house.mode == "shadow"
        assert house.question_type == "noul"
        assert house.jev_answer is True
        assert house.jev_probability == pytest.approx(0.97)
        assert house.fallback_answer is True
        assert house.agreement is True
        assert house.decided_by == "fallback"
        assert house.jev_model == "jev-1.14.0"
        assert house.jev_model_answered == "jev-1.14.0"
        assert house.fallback_model == "claude-haiku-5-5"
        assert house.input_tokens == 321
        assert house.output_tokens == 9
        assert house.latency_ms is not None
        assert house.fallback_latency_ms is not None
        assert house.threshold == {"yes": 0.90, "no": 0.10, "min_probability": 0.90}
        assert house.jev_error == ""
        walls = rows["wall_kind"]
        assert walls.jev_answer == "wood"
        assert walls.jev_probability == pytest.approx(0.93)
        assert walls.jev_detail["probabilities"]["wood"] == pytest.approx(0.93)
        assert walls.fallback_answer == "brick"
        assert walls.agreement is False
        assert house.request_id == walls.request_id == result.request_id

    def test_jev_error_falls_back_and_is_logged(self, settings):
        settings.JEV_MODE = "shadow"
        client = FakeJev(TimeoutError("read timed out"))
        fallback = RecordingFallback({"is_house_ad": True})

        result = judge.ask("ingest", "a1", {"title": "Namas"}, [HOUSE], fallback, client=client)

        assert result["is_house_ad"].value is True
        assert result["is_house_ad"].jev is None
        assert "TimeoutError" in result["is_house_ad"].jev_error
        assert result["is_house_ad"].agreement is None
        row = JudgmentLog.objects.get()
        assert "read timed out" in row.jev_error
        assert row.jev_answer is None
        assert row.jev_probability is None
        assert row.fallback_answer is True
        assert row.agreement is None

    def test_fallback_error_still_logs_jev_then_raises(self, settings):
        settings.JEV_MODE = "shadow"
        client = FakeJev(envelope({"is_house_ad": noul(0.2)}))
        fallback = RecordingFallback(error=RuntimeError("claude down"))

        with pytest.raises(RuntimeError, match="claude down"):
            judge.ask("ingest", 1, {}, [HOUSE], fallback, client=client)

        row = JudgmentLog.objects.get()
        assert row.jev_probability == pytest.approx(0.2)
        assert row.fallback_answer is None
        assert "claude down" in row.fallback_error
        assert row.decided_by == "none"

    def test_fallback_missing_an_answer_is_a_loud_error(self, settings):
        settings.JEV_MODE = "shadow"
        client = FakeJev(envelope({"is_house_ad": noul(0.5)}))

        with pytest.raises(ValueError, match="is_house_ad"):
            judge.ask("ingest", 1, {}, [HOUSE], lambda qs: {}, client=client)
        assert "is_house_ad" in JudgmentLog.objects.get().fallback_error

    def test_fallback_wrong_type_is_a_loud_error(self, settings):
        settings.JEV_MODE = "shadow"
        client = FakeJev(envelope({"is_house_ad": noul(0.5)}))
        with pytest.raises(ValueError):
            judge.ask("ingest", 1, {}, [HOUSE], lambda qs: {"is_house_ad": "yes"}, client=client)

    def test_without_fallback_only_jev_is_logged(self, settings):
        settings.JEV_MODE = "shadow"
        client = FakeJev(envelope({"is_house_ad": noul(0.99)}))

        result = judge.ask("ingest", 1, {}, [HOUSE], None, client=client)

        assert result["is_house_ad"].value is None
        assert result["is_house_ad"].decided_by == "none"
        assert result["is_house_ad"].jev.value is True
        row = JudgmentLog.objects.get()
        assert row.jev_answer is True
        assert row.fallback_answer is None
        assert row.fallback_latency_ms is None


# --- Mode: on ----------------------------------------------------------------------------


@pytest.mark.django_db
class TestOnMode:
    def test_jev_decides_when_beyond_threshold_and_fallback_covers_the_rest(self, settings):
        settings.JEV_MODE = "on"
        client = FakeJev(
            envelope(
                {
                    "is_house_ad": noul(0.03),  # at/below 0.10 → decisive "no"
                    "wall_kind": choice("wood", {"brick": 0.4, "wood": 0.5, "unknown": 0.1}),
                    "condition": score([0.02, 0.03, 0.95]),
                }
            )
        )
        fallback = RecordingFallback({"wall_kind": "brick"})

        result = judge.ask(
            "evaluation", 5, {}, [HOUSE, WALLS, STATE_OF_REPAIR], fallback, client=client
        )

        assert fallback.calls == [["wall_kind"]], "only undecided questions go to the fallback"
        assert result["is_house_ad"].value is False
        assert result["is_house_ad"].decided_by == "jev"
        assert result["condition"].value == 2
        assert result["condition"].decided_by == "jev"
        assert result["wall_kind"].value == "brick"
        assert result["wall_kind"].decided_by == "fallback"
        assert result["wall_kind"].agreement is False
        assert result.values == {"is_house_ad": False, "wall_kind": "brick", "condition": 2}

        rows = {r.question: r for r in JudgmentLog.objects.all()}
        assert rows["is_house_ad"].decided_by == "jev"
        assert rows["is_house_ad"].fallback_answer is None
        assert rows["is_house_ad"].agreement is None
        assert rows["wall_kind"].decided_by == "fallback"
        assert rows["wall_kind"].fallback_answer == "brick"
        assert rows["condition"].jev_answer == 2

    @pytest.mark.parametrize(
        ("p", "decided_by", "value"),
        [
            (0.90, "jev", True),  # at the threshold counts
            (0.10, "jev", False),
            (0.899, "fallback", True),
            (0.101, "fallback", True),
        ],
    )
    def test_noul_threshold_boundaries(self, settings, p, decided_by, value):
        settings.JEV_MODE = "on"
        client = FakeJev(envelope({"is_house_ad": noul(p)}))
        result = judge.ask(
            "x", 1, {}, [HOUSE], RecordingFallback({"is_house_ad": True}), client=client
        )
        assert result["is_house_ad"].decided_by == decided_by
        assert result["is_house_ad"].value is value

    def test_per_question_threshold_from_settings(self, settings):
        settings.JEV_MODE = "on"
        settings.JEV_THRESHOLDS = {"is_house_ad": {"yes": 0.99}}
        client = FakeJev(envelope({"is_house_ad": noul(0.95)}))
        fallback = RecordingFallback({"is_house_ad": False})
        result = judge.ask("x", 1, {}, [HOUSE], fallback, client=client)
        assert result["is_house_ad"].decided_by == "fallback"
        assert JudgmentLog.objects.get().threshold["yes"] == 0.99

    def test_jev_error_hands_every_question_to_the_fallback(self, settings):
        settings.JEV_MODE = "on"
        client = FakeJev(ConnectionError("dns"))
        fallback = RecordingFallback({"is_house_ad": True, "wall_kind": "wood"})
        result = judge.ask("x", 1, {}, [HOUSE, WALLS], fallback, client=client)
        assert fallback.calls == [["is_house_ad", "wall_kind"]]
        assert result.values == {"is_house_ad": True, "wall_kind": "wood"}
        assert JudgmentLog.objects.filter(jev_error__contains="dns").count() == 2

    def test_all_decisive_means_no_fallback_call(self, settings):
        settings.JEV_MODE = "on"
        client = FakeJev(envelope({"is_house_ad": noul(0.995)}))
        fallback = RecordingFallback({"is_house_ad": False})
        result = judge.ask("x", 1, {}, [HOUSE], fallback, client=client)
        assert fallback.calls == []
        assert result["is_house_ad"].value is True
        assert JudgmentLog.objects.get().fallback_latency_ms is None

    def test_undecided_without_fallback_gives_none(self, settings):
        settings.JEV_MODE = "on"
        client = FakeJev(envelope({"is_house_ad": noul(0.5)}))
        result = judge.ask("x", 1, {}, [HOUSE], None, client=client)
        assert result["is_house_ad"].value is None
        assert result["is_house_ad"].decided_by == "none"


# --- Response validation ----------------------------------------------------------------


@pytest.mark.django_db
class TestResponseValidation:
    @pytest.mark.parametrize(
        "answer",
        [
            {"type": "noul", "noul": 1.5},
            {"type": "noul", "noul": "0.9"},
            {"type": "noul", "noul": True},
            {"type": "noul"},
            {"type": "choice", "choice": "x", "confidence": 0.9, "probabilities": {"x": 1.0}},
        ],
    )
    def test_a_malformed_answer_only_drops_that_question(self, settings, answer):
        settings.JEV_MODE = "on"
        client = FakeJev(
            envelope(
                {
                    "is_house_ad": answer,
                    "wall_kind": choice("wood", {"brick": 0.03, "wood": 0.95, "unknown": 0.02}),
                }
            )
        )
        fallback = RecordingFallback({"is_house_ad": False, "wall_kind": "brick"})

        result = judge.ask("x", 1, {}, [HOUSE, WALLS], fallback, client=client)

        assert fallback.calls == [["is_house_ad"]]
        assert result["is_house_ad"].decided_by == "fallback"
        assert result["is_house_ad"].jev_error
        assert result["wall_kind"].decided_by == "jev"

    def test_missing_answer_is_an_error_for_that_question(self, settings):
        settings.JEV_MODE = "shadow"
        client = FakeJev(envelope({}))
        result = judge.ask(
            "x", 1, {}, [HOUSE], RecordingFallback({"is_house_ad": True}), client=client
        )
        assert "no answer" in result["is_house_ad"].jev_error

    @pytest.mark.parametrize("raw", [None, [], {"model": "jev-1.14.0"}, {"answers": []}])
    def test_malformed_envelope_falls_back_entirely(self, settings, raw):
        settings.JEV_MODE = "on"
        result = judge.ask(
            "x", 1, {}, [HOUSE], RecordingFallback({"is_house_ad": True}), client=FakeJev(raw)
        )
        assert result["is_house_ad"].decided_by == "fallback"
        assert result["is_house_ad"].jev_error

    def test_missing_usage_is_tolerated(self, settings):
        settings.JEV_MODE = "shadow"
        client = FakeJev({"model": "jev-1.14.0", "answers": {"is_house_ad": noul(0.9)}})
        judge.ask("x", 1, {}, [HOUSE], None, client=client)
        row = JudgmentLog.objects.get()
        assert row.input_tokens is None
        assert row.jev_answer is True

    def test_score_value_is_the_most_probable_level(self):
        answer = judge.parse_answer(
            STATE_OF_REPAIR, score([0.1, 0.6, 0.3]), judge.threshold_for("condition")
        )
        assert answer.value == 1
        assert answer.probability == pytest.approx(0.6)
        assert answer.decisive is False

    def test_score_with_wrong_levels_is_rejected(self):
        with pytest.raises(JevResponseError):
            judge.parse_answer(STATE_OF_REPAIR, score([0.5, 0.5]), judge.threshold_for("condition"))

    def test_type_mismatch_is_rejected(self):
        with pytest.raises(JevResponseError):
            judge.parse_answer(HOUSE, choice("wood", {"wood": 1.0}), judge.threshold_for("q"))


# --- Configuration problems never block ------------------------------------------------


@pytest.mark.django_db
class TestJevUnavailable:
    def test_missing_key_falls_back_with_a_clear_error(self, settings, monkeypatch):
        settings.JEV_MODE = "shadow"
        monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
        monkeypatch.setattr(judge, "_default_client", None)

        result = judge.ask("x", 1, {}, [HOUSE], RecordingFallback({"is_house_ad": True}))

        assert result["is_house_ad"].value is True
        assert "TYPESAFE_API_KEY" in result["is_house_ad"].jev_error

    @pytest.mark.parametrize("model", ["jev-latest", "jev-preview", ""])
    def test_unpinned_model_is_refused_and_falls_back(self, settings, model):
        settings.JEV_MODE = "on"
        settings.JEV_MODEL = model
        client = FakeJev(envelope({"is_house_ad": noul(0.99)}))

        result = judge.ask(
            "x", 1, {}, [HOUSE], RecordingFallback({"is_house_ad": False}), client=client
        )

        assert client.calls == []
        assert result["is_house_ad"].decided_by == "fallback"
        assert "pinned" in result["is_house_ad"].jev_error

    def test_answered_model_differs_from_pin_is_recorded(self, settings):
        settings.JEV_MODE = "shadow"
        client = FakeJev(envelope({"is_house_ad": noul(0.99)}, model="jev-1.14.1"))
        result = judge.ask("x", 1, {}, [HOUSE], None, client=client)
        assert result.jev_model == "jev-1.14.1"
        row = JudgmentLog.objects.get()
        assert (row.jev_model, row.jev_model_answered) == ("jev-1.14.0", "jev-1.14.1")


# --- Arguments ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestArguments:
    def test_duplicate_question_names_are_rejected(self, settings):
        settings.JEV_MODE = "shadow"
        with pytest.raises(ValueError, match="duplicate"):
            judge.ask("x", 1, {}, [HOUSE, HOUSE], None, client=FakeJev(None))

    def test_empty_questions_are_rejected(self, settings):
        settings.JEV_MODE = "shadow"
        with pytest.raises(ValueError):
            judge.ask("x", 1, {}, [], None, client=FakeJev(None))

    def test_empty_kind_is_rejected(self, settings):
        settings.JEV_MODE = "shadow"
        with pytest.raises(ValueError):
            judge.ask("", 1, {}, [HOUSE], None, client=FakeJev(None))


# --- State hygiene -------------------------------------------------------------------------


class TestState:
    def test_contact_keys_and_patterns_are_removed_before_sending(self):
        state = {
            "title": "Namas Kretingoje",
            "phone": "+370 612 34567",
            "seller": {"contact_email": "a@b.lt", "name_of_village": "Kurmaičiai"},
            "description": "Skambinkite +370 612 34567 arba 8 612 34567, rašykite jonas@pastas.lt.",
            "price": Decimal("85000.00"),
            "notes": ["kaina 85 000 €", "tel. 861234567"],
        }
        clean = judge.clean_state(state)

        assert "phone" not in clean
        assert "contact_email" not in clean["seller"]
        assert clean["seller"]["name_of_village"] == "Kurmaičiai"
        assert "612" not in clean["description"]
        assert "@" not in clean["description"]
        assert clean["price"] == 85000
        assert clean["notes"][0] == "kaina 85 000 €", "prices are not phone numbers"
        assert "861234567" not in clean["notes"][1]

    def test_non_finite_numbers_become_null(self):
        assert judge.clean_state({"x": float("nan")}) == {"x": None}

    @pytest.mark.django_db
    def test_listing_state_is_a_trimmed_whitelist(self):
        listing = baker.make(
            "listings.Listing",
            title="Namas",
            description="Tel. 8 612 34567",
            price=Decimal("120000"),
            area_sqm=Decimal("98.5"),
            raw_data={"phone": "+37061234567"},
            heating_type="",
        )
        state = judge.listing_state(listing)

        assert state["title"] == "Namas"
        assert state["price"] == 120000
        assert state["area_sqm"] == 98.5
        assert "raw_data" not in state
        assert "source_url" not in state
        assert "heating_type" not in state, "empty values are trimmed"
        assert "612" not in state["description"]

    def test_listing_state_accepts_a_scraped_item_mapping(self):
        state = judge.listing_state({"title": "Sodyba", "rooms": 4, "phone": "+37061234567"})
        assert state == {"title": "Sodyba", "rooms": 4}

    @pytest.mark.django_db
    def test_ask_sends_the_cleaned_state(self, settings):
        settings.JEV_MODE = "shadow"
        client = FakeJev(envelope({"is_house_ad": noul(0.9)}))
        judge.ask("x", 1, {"title": "Namas", "phone": "+37061234567"}, [HOUSE], None, client=client)
        assert client.calls[0]["state"] == {"title": "Namas"}


class TestImportability:
    def test_module_imports_without_the_sdk_or_django_setup(self):
        """The scrapers image imports backend code and may lack typesafe-sdk until rebuilt."""
        import subprocess
        import sys
        from pathlib import Path

        code = (
            "import sys; sys.modules['typesafe_sdk'] = None\n"
            "import apps.classifier.judge as j\n"
            "assert callable(j.ask)\n"
        )
        backend_dir = Path(judge.__file__).resolve().parents[2]
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=backend_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr

    def test_sdk_client_reports_a_missing_sdk(self, monkeypatch):
        import sys

        monkeypatch.setitem(sys.modules, "typesafe_sdk", None)  # `import typesafe_sdk` fails
        with pytest.raises(judge.JevUnavailableError, match="typesafe-sdk"):
            judge.SdkJevClient(api_key="k", max_retries=0)
