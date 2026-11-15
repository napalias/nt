"""The one place that asks Jev (TypeSafe System One), applies the off/shadow/on switch, and
logs what was decided.

Jev answers typed questions about a ``state`` with calibrated probabilities. It cannot write
free text and is weak at arithmetic, counting and dates, so numeric rules stay in code and
only judgments come here: yes/no (a "noul"), choice, and score questions.

API
---
``ask(kind, subject_id, state, questions, fallback=None, *, fallback_model="", client=None)``
returns a ``JudgeResult``. ``result[name].value`` is the decided answer (``bool`` for yes/no,
the option name for a choice, the level index for a score, or ``None`` when nothing decided)
and ``result[name].decided_by`` says who decided it: ``"jev"``, ``"fallback"`` or ``"none"``.

- ``kind``: what is being judged, e.g. ``"evaluation"``, ``"dedup"``, ``"ingest"`` (<= 32 chars).
- ``subject_id``: the listing id, or a pair key such as ``"12:34"`` for dedup (<= 64 chars).
- ``state``: a JSON-able dict about the subject. ``listing_state()`` builds the standard
  trimmed one from a Listing or a scraped item. Contact keys and phone/email patterns are
  stripped from whatever is passed before it leaves the process.
- ``questions``: ``Question.yes_no / .choice / .score`` objects with unique names. All of them
  go to Jev in one request.
- ``fallback``: today's decider, e.g. a Claude call. It receives the list of questions it must
  answer and returns ``{question name: value}`` with a value for each (``None`` means it
  abstains). Its exceptions propagate, as they do today.

Modes (``settings.JEV_MODE``)
-----------------------------
- ``off``: today's path. Only the fallback runs. Jev is not called and nothing is logged.
- ``shadow``: the fallback decides every question. Jev is asked too, and both answers go to
  ``JudgmentLog`` with their agreement.
- ``on``: Jev decides each question whose probability clears its threshold. The fallback is
  called once, only for the questions Jev left undecided. Everything that ran is logged.

A Jev error, timeout, malformed answer, missing key, missing SDK or unpinned ``JEV_MODEL``
never raises: it is logged on the rows (``jev_error``) and the fallback decides. Bad
configuration of our own (an unknown ``JEV_MODE``, impossible thresholds) and a fallback
that breaks its contract raise loudly.

Thresholds come from ``settings.JEV_DEFAULT_THRESHOLD`` with per-question overrides in
``settings.JEV_THRESHOLDS``. Yes/no: Jev decides yes at ``P(yes) >= yes`` and no at
``P(yes) <= no``. Choice and score: Jev decides when its top option has
``P >= min_probability``. Agreement in the log compares Jev's most likely answer with the
fallback's, whatever the threshold, so the go-live report can replay other thresholds.

Example::

    from apps.classifier import judge
    from apps.classifier.judge import Question

    questions = [
        Question.yes_no("is_house_ad", "Is `title` and `description` an ad selling a house?"),
        Question.choice(
            "wall_kind",
            "What are the outer walls made of, per `description`?",
            {"brick": "Brick or blocks", "wood": "Timber or logs", "unknown": "Not stated"},
        ),
    ]

    def claude_decides(pending):  # today's decider; answers only what it is handed
        data = classify_with_claude(listing)
        return {q.name: data[q.name] for q in pending}

    result = judge.ask(
        "ingest", listing.pk, judge.listing_state(listing), questions,
        claude_decides, fallback_model=settings.CLAUDE_FAST_MODEL,
    )
    if result["is_house_ad"].value is False:
        ...

The module imports without Django being set up and without ``typesafe-sdk`` installed, so the
scrapers container can import it; the SDK is loaded on the first real Jev call.
``TYPESAFE_API_KEY`` is read from ``os.environ`` at that call and never logged.
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

MODES = ("off", "shadow", "on")
QUESTION_TYPES = ("noul", "choice", "score")
# Documented API limits for one question.
MAX_CHOICE_OPTIONS = 255
MAX_SCORE_LEVELS = 10
# Lengths match the JudgmentLog columns.
MAX_KIND_LENGTH = 32
MAX_SUBJECT_LENGTH = 64
MAX_ERROR_LENGTH = 1000

_QUESTION_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# A versioned ID such as jev-1.14.0; aliases (jev-latest, jev-preview) move between releases.
_PINNED_MODEL_RE = re.compile(r"^jev-\d+\.\d+\.\d+$")
_THRESHOLD_KEYS = ("yes", "no", "min_probability")
# Floating-point slack when checking that a probability lies in [0, 1].
_PROBABILITY_SLACK = 1e-9

Value = bool | str | int
DecidedBy = Literal["jev", "fallback", "none"]
Fallback = Callable[[Sequence["Question"]], Mapping[str, Any]]


class JevError(Exception):
    """Jev could not give a usable answer. ask() catches it and the fallback decides."""


class JevUnavailableError(JevError):
    """Jev cannot be called here: no key, no SDK, or an unpinned model."""


class JevResponseError(JevError):
    """Jev answered, but the answer does not have the documented shape."""


# --- Questions ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Question:
    """One typed question. Build it with ``yes_no``, ``choice`` or ``score``.

    ``name`` is lowercase snake_case. It keys the request, the answer, the threshold override
    in settings and the JudgmentLog row.
    """

    name: str
    type: str
    instructions: str
    criteria: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _QUESTION_NAME_RE.match(self.name):
            raise ValueError(f"question name {self.name!r} must be lowercase snake_case, <= 64")
        if self.type not in QUESTION_TYPES:
            raise ValueError(f"{self.name}: question type must be one of {QUESTION_TYPES}")
        if not isinstance(self.instructions, str) or not self.instructions.strip():
            raise ValueError(f"{self.name}: instructions must be non-empty text")
        object.__setattr__(self, "criteria", self._checked_criteria())

    def _checked_criteria(self) -> Any:
        if self.type == "noul":
            if self.criteria is None:
                return None
            if not isinstance(self.criteria, Mapping) or not set(self.criteria) <= {
                "true",
                "false",
            }:
                raise ValueError(f"{self.name}: yes/no criteria take only 'true' and 'false'")
            return {k: _require_text(v, self.name) for k, v in self.criteria.items()}
        if self.type == "choice":
            if not isinstance(self.criteria, Mapping) or not self.criteria:
                raise ValueError(f"{self.name}: a choice needs at least one option")
            if len(self.criteria) > MAX_CHOICE_OPTIONS:
                raise ValueError(f"{self.name}: a choice takes at most {MAX_CHOICE_OPTIONS}")
            options = {}
            for option, description in self.criteria.items():
                if not isinstance(option, str) or not option.strip():
                    raise ValueError(f"{self.name}: option names must be non-empty text")
                options[option] = (
                    None if description is None else _require_text(description, self.name)
                )
            return options
        # score
        if isinstance(self.criteria, str | bytes) or not isinstance(self.criteria, Sequence):
            raise ValueError(f"{self.name}: a score needs an ordered list of level descriptions")
        if not 1 <= len(self.criteria) <= MAX_SCORE_LEVELS:
            raise ValueError(f"{self.name}: a score takes 1 to {MAX_SCORE_LEVELS} levels")
        return tuple(_require_text(level, self.name) for level in self.criteria)

    @classmethod
    def yes_no(
        cls, name: str, instructions: str, *, yes: str | None = None, no: str | None = None
    ) -> Question:
        """A yes/no question. ``yes`` / ``no`` optionally say what counts as each answer."""
        criteria = {k: v for k, v in (("true", yes), ("false", no)) if v is not None}
        return cls(name, "noul", instructions, criteria or None)

    @classmethod
    def choice(cls, name: str, instructions: str, options: Mapping[str, str | None]) -> Question:
        """Pick one option. Keys are option names, values say when each applies (or None)."""
        return cls(name, "choice", instructions, options)

    @classmethod
    def score(cls, name: str, instructions: str, levels: Sequence[str]) -> Question:
        """Rate against ordered levels; the answer is the level index, starting at 0."""
        return cls(name, "score", instructions, levels)

    @property
    def options(self) -> tuple[str, ...]:
        """Choice option names, or score level indexes as strings (the API's answer keys)."""
        if self.type == "choice":
            return tuple(self.criteria)
        if self.type == "score":
            return tuple(str(i) for i in range(len(self.criteria)))
        return ()

    def to_request(self) -> dict[str, Any]:
        """This question in the System One request shape."""
        body: dict[str, Any] = {"type": self.type, "instructions": self.instructions}
        if self.type == "score":
            body["criteria"] = list(self.criteria)
        elif self.criteria:
            body["criteria"] = dict(self.criteria)
        return body

    def check_value(self, value: Any) -> Value | None:
        """Return ``value`` if it is a valid answer to this question (or None), else raise."""
        if value is None:
            return None
        if self.type == "noul":
            ok = isinstance(value, bool)
        elif self.type == "choice":
            ok = isinstance(value, str) and value in self.criteria
        else:
            ok = (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 0 <= value < len(self.criteria)
            )
        if not ok:
            raise ValueError(f"{self.name}: {value!r} is not a valid {self.type} answer")
        return value


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: criteria descriptions must be non-empty text")
    return value


# --- Settings ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Threshold:
    yes: float
    no: float
    min_probability: float

    def as_dict(self) -> dict[str, float]:
        return {"yes": self.yes, "no": self.no, "min_probability": self.min_probability}


def current_mode() -> str:
    mode = str(settings.JEV_MODE).strip().lower()
    if mode not in MODES:
        raise ImproperlyConfigured(f"JEV_MODE must be one of {MODES}, got {settings.JEV_MODE!r}")
    return mode


def threshold_for(question_name: str) -> Threshold:
    """JEV_DEFAULT_THRESHOLD with this question's JEV_THRESHOLDS override on top."""
    merged = {**settings.JEV_DEFAULT_THRESHOLD, **settings.JEV_THRESHOLDS.get(question_name, {})}
    unknown = set(merged) - set(_THRESHOLD_KEYS)
    missing = set(_THRESHOLD_KEYS) - set(merged)
    if unknown or missing:
        raise ImproperlyConfigured(
            f"Jev threshold for {question_name!r}: unknown keys {sorted(unknown)}, "
            f"missing keys {sorted(missing)}"
        )
    for key, value in merged.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise ImproperlyConfigured(f"Jev threshold {question_name}.{key} must be a number")
    t = Threshold(float(merged["yes"]), float(merged["no"]), float(merged["min_probability"]))
    if not 0.0 <= t.no <= 0.5 <= t.yes <= 1.0:
        raise ImproperlyConfigured(
            f"Jev threshold for {question_name!r} needs 0 <= no <= 0.5 <= yes <= 1, got {t}"
        )
    if not 0.0 < t.min_probability <= 1.0:
        raise ImproperlyConfigured(
            f"Jev threshold for {question_name!r} needs 0 < min_probability <= 1, got {t}"
        )
    return t


# --- State -------------------------------------------------------------------------------

# The listing fields a judgment may see. No contact data, no raw_data, no URLs.
STATE_FIELDS = (
    "title",
    "description",
    "property_type",
    "listing_type",
    "price",
    "currency",
    "area_sqm",
    "plot_area_ares",
    "rooms",
    "floor",
    "total_floors",
    "year_built",
    "building_type",
    "heating_type",
    "energy_class",
    "is_new_construction",
    "address_raw",
    "city",
    "municipality",
    "district",
)

_CONTACT_KEY_RE = re.compile(r"phone|e_?mail|contact|telefon|kontakt|(?:^|_)tel(?:$|_)", re.I)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Lithuanian numbers (+370 or a leading 8, then 8 digits) and other +country numbers. A price
# such as "85 000" has too few digits to match.
_PHONE_RE = re.compile(
    r"\+\s?370(?:[\s\-().]*\d){8}(?!\d)"
    r"|(?<![\d+])8(?:[\s\-().]*\d){8}(?!\d)"
    r"|\+\d(?:[\s\-().]*\d){7,14}(?!\d)"
)
_REDACTED = "[removed]"


def _redact(text: str) -> str:
    return _PHONE_RE.sub(_REDACTED, _EMAIL_RE.sub(_REDACTED, text))


def clean_state(value: Any) -> Any:
    """A JSON-safe copy with contact keys dropped and phone/email patterns redacted."""
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(k): clean_state(v) for k, v in value.items() if not _CONTACT_KEY_RE.search(str(k))
        }
    if isinstance(value, list | tuple):
        return [clean_state(v) for v in value]
    return _redact(str(value))


def listing_state(source: Any, fields: Sequence[str] = STATE_FIELDS) -> dict[str, Any]:
    """The standard trimmed state for a Listing or a scraped item mapping.

    Only ``fields`` are read; empty values are dropped, contact data is stripped.
    """
    if isinstance(source, Mapping):
        values = {f: source.get(f) for f in fields}
    else:
        values = {f: getattr(source, f, None) for f in fields}
    trimmed = {f: v for f, v in values.items() if v is not None and v not in ("", [], {})}
    return clean_state(trimmed)


# --- Answers -----------------------------------------------------------------------------


@dataclass(frozen=True)
class JevAnswer:
    """Jev's answer to one question.

    ``value`` is the decided answer when ``decisive``, otherwise Jev's most likely answer.
    ``probability`` is P(yes) for yes/no and P(value) for choice and score. ``detail`` keeps
    the documented fields (probabilities, confidence, expected score) for the log.
    """

    value: Value
    probability: float
    decisive: bool
    detail: dict[str, Any]


@dataclass(frozen=True)
class Judgment:
    question: str
    value: Value | None
    decided_by: DecidedBy
    jev: JevAnswer | None
    jev_error: str
    fallback_value: Value | None
    agreement: bool | None


@dataclass(frozen=True)
class JudgeResult:
    mode: str
    request_id: uuid.UUID | None  # None in off mode: nothing was sent or logged
    judgments: Mapping[str, Judgment]
    jev_model: str = ""  # the model ID that answered, from the response

    def __getitem__(self, question_name: str) -> Judgment:
        return self.judgments[question_name]

    @property
    def values(self) -> dict[str, Value | None]:
        return {name: j.value for name, j in self.judgments.items()}


def _probability(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise JevResponseError(f"{where} must be a number, got {value!r}")
    if not -_PROBABILITY_SLACK <= value <= 1 + _PROBABILITY_SLACK:
        raise JevResponseError(f"{where} must be between 0 and 1, got {value!r}")
    return min(1.0, max(0.0, float(value)))


def _optional_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return float(value)


def _probabilities(raw: Any, expected: tuple[str, ...], name: str) -> dict[str, float]:
    if not isinstance(raw, Mapping) or set(raw) != set(expected):
        keys = sorted(raw) if isinstance(raw, Mapping) else raw
        raise JevResponseError(f"{name}: probabilities must cover {list(expected)}, got {keys}")
    return {key: _probability(raw[key], f"{name}.probabilities[{key}]") for key in expected}


def parse_answer(question: Question, answer: Any, threshold: Threshold) -> JevAnswer:
    """Validate one documented System One answer and apply the threshold."""
    if not isinstance(answer, Mapping):
        raise JevResponseError(f"{question.name}: answer is not an object")
    if answer.get("type") != question.type:
        raise JevResponseError(
            f"{question.name}: expected a {question.type} answer, got {answer.get('type')!r}"
        )

    if question.type == "noul":
        p = _probability(answer.get("noul"), f"{question.name}.noul")
        if p >= threshold.yes:
            return JevAnswer(True, p, True, {"noul": p})
        if p <= threshold.no:
            return JevAnswer(False, p, True, {"noul": p})
        return JevAnswer(p >= 0.5, p, False, {"noul": p})

    probabilities = _probabilities(answer.get("probabilities"), question.options, question.name)
    detail: dict[str, Any] = {
        "confidence": _optional_number(answer.get("confidence")),
        "probabilities": probabilities,
    }
    if question.type == "choice":
        picked = answer.get("choice")
        if picked not in probabilities:
            raise JevResponseError(f"{question.name}: choice {picked!r} is not an option")
        p = probabilities[picked]
        return JevAnswer(picked, p, p >= threshold.min_probability, detail)

    # score: the value is the most probable level; the expected score is kept for the log.
    top = max(range(len(question.options)), key=lambda i: probabilities[str(i)])
    p = probabilities[str(top)]
    detail["score"] = _optional_number(answer.get("score"))
    return JevAnswer(top, p, p >= threshold.min_probability, detail)


# --- Client ------------------------------------------------------------------------------


class JevClient(Protocol):
    """What ask() needs from a client: the System One response as a JSON-shaped mapping."""

    def system_one(
        self, *, state: Any, questions: Mapping[str, dict], model: str, timeout: float
    ) -> Mapping[str, Any]: ...


class SdkJevClient:
    """JevClient on the official ``typesafe-sdk`` (sync TypeSafeClient)."""

    def __init__(self, *, api_key: str, max_retries: int, transport: Any = None) -> None:
        try:
            import typesafe_sdk
        except ImportError as exc:
            raise JevUnavailableError(
                "typesafe-sdk is not installed in this image; rebuild it after the "
                "pyproject change (docker compose build)"
            ) from exc
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "retry": typesafe_sdk.RetryPolicy(max_retries=max_retries),
            # Bodies are one listing: compression saves nothing and keeps requests readable.
            "compression": False,
        }
        if transport is not None:
            kwargs["transport"] = transport
        self._client = typesafe_sdk.TypeSafeClient(**kwargs)

    def system_one(
        self, *, state: Any, questions: Mapping[str, dict], model: str, timeout: float
    ) -> Mapping[str, Any]:
        response = self._client.system_one(
            state=state, questions=questions, model=model, timeout=timeout
        )
        return response.model_dump(mode="json")


# One client per process, so its connection pool is reused. Built on the first real call.
_default_client: JevClient | None = None


def get_default_client() -> JevClient:
    global _default_client
    if _default_client is None:
        api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
        if not api_key:
            raise JevUnavailableError(
                "TYPESAFE_API_KEY is not set in the environment; add it to .env and restart"
            )
        _default_client = SdkJevClient(api_key=api_key, max_retries=settings.JEV_MAX_RETRIES)
    return _default_client


# --- ask() -------------------------------------------------------------------------------


@dataclass
class _JevOutcome:
    answers: dict[str, JevAnswer]
    errors: dict[str, str]
    model: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _is_expected_failure(exc: BaseException) -> bool:
    """Network, server and configuration failures: logged in one line, no traceback."""
    return isinstance(exc, JevError | TimeoutError | ConnectionError) or type(
        exc
    ).__module__.startswith("typesafe_sdk")


def _ask_jev(
    state: Any,
    questions: Sequence[Question],
    thresholds: Mapping[str, Threshold],
    client: JevClient | None,
) -> _JevOutcome:
    model = str(settings.JEV_MODEL).strip()
    started: float | None = None
    try:
        if not _PINNED_MODEL_RE.match(model):
            raise JevUnavailableError(
                f"JEV_MODEL {model!r} is not a pinned version such as jev-1.14.0"
            )
        jev = client if client is not None else get_default_client()
        payload = {q.name: q.to_request() for q in questions}
        started = time.monotonic()
        raw = jev.system_one(
            state=clean_state(state),
            questions=payload,
            model=model,
            timeout=float(settings.JEV_TIMEOUT_S),
        )
        latency_ms = _elapsed_ms(started)
        if not isinstance(raw, Mapping) or not isinstance(raw.get("answers"), Mapping):
            raise JevResponseError("response has no answers object")
    except Exception as exc:  # noqa: BLE001 - any Jev failure falls back; it must not block
        message = _describe(exc)
        logger.warning(
            "Jev request failed, the fallback decides: %s",
            message,
            exc_info=not _is_expected_failure(exc),
        )
        return _JevOutcome(
            answers={},
            errors={q.name: message for q in questions},
            latency_ms=_elapsed_ms(started) if started is not None else None,
        )

    usage = raw.get("usage") if isinstance(raw.get("usage"), Mapping) else {}
    outcome = _JevOutcome(
        answers={},
        errors={},
        model=str(raw.get("model") or "")[:64],
        input_tokens=_count(usage.get("input_tokens")),
        output_tokens=_count(usage.get("output_tokens")),
        latency_ms=latency_ms,
    )
    for q in questions:
        try:
            if q.name not in raw["answers"]:
                raise JevResponseError(f"{q.name}: no answer for this question")
            outcome.answers[q.name] = parse_answer(q, raw["answers"][q.name], thresholds[q.name])
        except JevResponseError as exc:
            outcome.errors[q.name] = _describe(exc)
            logger.warning("Jev answer unusable, the fallback decides: %s", exc)
    return outcome


def _run_fallback(
    fallback: Fallback | None, questions: Sequence[Question]
) -> tuple[dict[str, Value | None], int | None]:
    if fallback is None or not questions:
        return {}, None
    started = time.monotonic()
    raw = fallback(list(questions))
    latency_ms = _elapsed_ms(started)
    if not isinstance(raw, Mapping):
        raise ValueError(f"fallback must return a mapping of question name to answer, got {raw!r}")
    values: dict[str, Value | None] = {}
    for q in questions:
        if q.name not in raw:
            raise ValueError(f"fallback returned no answer for {q.name!r} (use None to abstain)")
        values[q.name] = q.check_value(raw[q.name])
    return values, latency_ms


def _judgment(
    mode: str, question: Question, jev: _JevOutcome, fallback_values: Mapping[str, Any]
) -> Judgment:
    answer = jev.answers.get(question.name)
    fallback_value = fallback_values.get(question.name)
    if mode == "on" and answer is not None and answer.decisive:
        value, decided_by = answer.value, "jev"
    elif fallback_value is not None:
        value, decided_by = fallback_value, "fallback"
    else:
        value, decided_by = None, "none"
    agreement = (
        answer.value == fallback_value
        if answer is not None and fallback_value is not None
        else None
    )
    return Judgment(
        question=question.name,
        value=value,
        decided_by=decided_by,
        jev=answer,
        jev_error=jev.errors.get(question.name, ""),
        fallback_value=fallback_value,
        agreement=agreement,
    )


def _validate_call(kind: str, subject: str, questions: Sequence[Question]) -> None:
    if not isinstance(kind, str) or not kind.strip() or len(kind) > MAX_KIND_LENGTH:
        raise ValueError(f"kind must be non-empty text of at most {MAX_KIND_LENGTH} characters")
    if not subject or len(subject) > MAX_SUBJECT_LENGTH:
        raise ValueError(f"subject_id must be 1 to {MAX_SUBJECT_LENGTH} characters")
    if not questions:
        raise ValueError("ask() needs at least one question")
    names = []
    for q in questions:
        if not isinstance(q, Question):
            raise ValueError(f"questions must be Question objects, got {q!r}")
        names.append(q.name)
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError(f"duplicate question names: {duplicates}")


def ask(
    kind: str,
    subject_id: Any,
    state: Any,
    questions: Sequence[Question],
    fallback: Fallback | None = None,
    *,
    fallback_model: str = "",
    client: JevClient | None = None,
) -> JudgeResult:
    """Decide ``questions`` about ``state`` under JEV_MODE. See the module docstring."""
    questions = list(questions)
    subject = str(subject_id)
    _validate_call(kind, subject, questions)
    mode = current_mode()

    if mode == "off":
        values, _ = _run_fallback(fallback, questions)
        nothing = _JevOutcome(answers={}, errors={})
        return JudgeResult(
            mode=mode,
            request_id=None,
            judgments={q.name: _judgment(mode, q, nothing, values) for q in questions},
        )

    thresholds = {q.name: threshold_for(q.name) for q in questions}
    request_id = uuid.uuid4()
    jev = _ask_jev(state, questions, thresholds, client)

    if mode == "shadow":
        pending = questions
    else:
        pending = [
            q for q in questions if not (q.name in jev.answers and jev.answers[q.name].decisive)
        ]
    sent_to_fallback = {q.name for q in pending} if fallback is not None else set()

    fallback_values: dict[str, Value | None] = {}
    fallback_latency_ms: int | None = None
    fallback_error = ""
    try:
        fallback_values, fallback_latency_ms = _run_fallback(fallback, pending)
    except Exception as exc:
        fallback_error = _describe(exc)
        raise
    finally:
        judgments = {q.name: _judgment(mode, q, jev, fallback_values) for q in questions}
        _write_logs(
            request_id=request_id,
            kind=kind,
            subject=subject,
            mode=mode,
            questions=questions,
            thresholds=thresholds,
            judgments=judgments,
            jev=jev,
            sent_to_fallback=sent_to_fallback,
            fallback_model=fallback_model,
            fallback_latency_ms=fallback_latency_ms,
            fallback_error=fallback_error,
        )
    return JudgeResult(mode=mode, request_id=request_id, judgments=judgments, jev_model=jev.model)


def _write_logs(
    *,
    request_id: uuid.UUID,
    kind: str,
    subject: str,
    mode: str,
    questions: Sequence[Question],
    thresholds: Mapping[str, Threshold],
    judgments: Mapping[str, Judgment],
    jev: _JevOutcome,
    sent_to_fallback: set[str],
    fallback_model: str,
    fallback_latency_ms: int | None,
    fallback_error: str,
) -> None:
    # Imported here so the module loads before Django apps are ready (scrapers container).
    from apps.classifier.models import JudgmentLog

    jev_model = str(settings.JEV_MODEL).strip()[:64]
    rows = []
    for q in questions:
        j = judgments[q.name]
        went_to_fallback = q.name in sent_to_fallback
        rows.append(
            JudgmentLog(
                request_id=request_id,
                kind=kind,
                subject_id=subject,
                question=q.name,
                question_type=q.type,
                mode=mode,
                decided_by=j.decided_by,
                jev_answer=j.jev.value if j.jev else None,
                jev_probability=j.jev.probability if j.jev else None,
                jev_detail=j.jev.detail if j.jev else {},
                jev_error=j.jev_error,
                threshold=thresholds[q.name].as_dict(),
                fallback_answer=j.fallback_value,
                fallback_error=fallback_error if went_to_fallback else "",
                agreement=j.agreement,
                jev_model=jev_model,
                jev_model_answered=jev.model,
                fallback_model=fallback_model[:64] if went_to_fallback else "",
                input_tokens=jev.input_tokens,
                output_tokens=jev.output_tokens,
                latency_ms=jev.latency_ms,
                fallback_latency_ms=fallback_latency_ms if went_to_fallback else None,
            )
        )
    JudgmentLog.objects.bulk_create(rows)
