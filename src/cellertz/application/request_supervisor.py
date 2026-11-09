"""Protein Design Supervisor analysis of one saved design-request revision.

The call is the configured chat completion. Recorded gaps stay labeled as
the deterministic check. The model summary and its own questions are stored
with the revision that was sent. No plan, journey, approval, or run is created.

The proposed OpenRouter model is ``openai/gpt-4.1-mini``. Selecting it requires
``CELLERTZ_CONVERSATION_PROVIDER=openrouter``, ``CELLERTZ_CONVERSATION_MODEL``,
and ``OPENROUTER_API_KEY`` in the server environment. This module does not read
or store the key. A live call is not made while the provider stays recorded.
"""

from __future__ import annotations

import inspect
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from cellertz.application.conversation import ConversationModel, ReadOnlyFact, RecordedFactsModel
from cellertz.application.request_draft import GENERATORS, DesignDrafting, _QUESTIONS, _questions

PATH = "/api/design-supervisor"
AGENT = "Protein Design Supervisor"
PROPOSED_OPENROUTER_MODEL = "openai/gpt-4.1-mini"
DEFAULT_MAX_CALLS = 8
DEFAULT_MAX_OUTPUT_TOKENS = 400
DEFAULT_TOKEN_BUDGET = 2000
DEFAULT_CALL_TIMEOUT_SECONDS = 30.0
_QUESTION = (
    "You are the Protein Design Supervisor. Write a short summary in English of the "
    "design objective you understand. Do not only repeat the saved text. "
    "The facts keep three sources separate. researcher_information is what the "
    "researcher supplied. server_configuration is the server's compatible "
    "configuration, such as BindCraft for the PDL1 pilot, and is not the "
    "researcher's text and not your decision. missing_or_ambiguous lists gaps "
    "already shown as Recorded gaps. Do not assume PD-1/PD-L1 blockade, a "
    "therapeutic use, affinity, length, or any other property the researcher "
    "did not state. Ask at most two questions, and only when they are needed to "
    "clarify this request. Do not repeat a recorded gap or a question the "
    "researcher already answered. Do not invent a question to fill the response. "
    "A question clarifies the request and is not an execution plan. "
    "When a question is about a known field, start that line with the field name and a colon, "
    "for example 'n_candidates: How many candidates should be requested?'. "
    "Known fields are target_id, objective, generator, n_candidates, length_min, length_max, seed, and filter_policy. "
    "Do not ask again about a field already listed as missing or already supplied. "
    "Put questions after a line that starts with 'Questions:'. If you have no question, write "
    "'Questions: none'. Do not plan, approve, authorize, or start a run."
)
_STALE = "The analysis was not run. The selected revision is not the current revision."
_MAX_SUPERVISOR_QUESTIONS = 2
_UNAVAILABLE = "The Supervisor model is not configured. No analysis was substituted."
_FAILED = "The Supervisor could not analyze this request. No scientific action was taken."
_LIMIT = "The Supervisor call limit has been reached. No analysis was run."
_MISMATCH = "The analysis was not run. The text does not match the saved revision."
_REJECTED = "The Supervisor analysis was not accepted."
_NOT_EXECUTED = "Model not run"
_NOT_INFORMED = "Not reported"
_BODY_KEYS = frozenset({"session_id", "request_id", "revision", "message"})
_EXECUTED = (
    "was approved",
    "has been approved",
    "was authorized",
    "has been authorized",
    "generation was started",
    "start a run",
)
_LIMITATIONS = (
    "Recorded gaps come from the deterministic draft check and are not model findings.",
    "A Supervisor question about the same known field as a recorded gap is dropped, even when the wording differs, and is not shown as a discovery.",
    "A Supervisor question that repeats a field the researcher already supplied is dropped, even when the wording differs.",
    "At most two Supervisor questions are shown. Extra questions are dropped.",
    "The check looks for a fixed list of phrases that claim approval, authorization, or a started run.",
    "A paraphrase that avoids those phrases is not detected.",
    "max_output_tokens is the max_tokens sent on this call. It is not a total cost ceiling.",
    "token_budget compares provider-reported total_tokens for this call. Missing usage is not accepted.",
    "The call is not retried.",
)


@dataclass(frozen=True)
class SupervisorLimits:
    max_model_calls: int = DEFAULT_MAX_CALLS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    token_budget: int = DEFAULT_TOKEN_BUDGET
    call_timeout_seconds: float = DEFAULT_CALL_TIMEOUT_SECONDS
    retries: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_model_calls": self.max_model_calls,
            "max_output_tokens": self.max_output_tokens,
            "token_budget": self.token_budget,
            "call_timeout_seconds": self.call_timeout_seconds,
            "retries": self.retries,
            "output_token_limit_is_per_call": True,
        }


def resolve_supervisor_limits(environ: Mapping[str, str] | None = None) -> SupervisorLimits:
    """Read call and token limits. The API key is not one of these variables."""

    env = os.environ if environ is None else environ
    return SupervisorLimits(
        max_model_calls=_positive_int(env.get("CELLERTZ_SUPERVISOR_MAX_CALLS", ""), DEFAULT_MAX_CALLS, 20),
        max_output_tokens=_positive_int(
            env.get("CELLERTZ_SUPERVISOR_MAX_OUTPUT_TOKENS", ""),
            DEFAULT_MAX_OUTPUT_TOKENS,
            2000,
        ),
        token_budget=_positive_int(env.get("CELLERTZ_SUPERVISOR_TOKEN_BUDGET", ""), DEFAULT_TOKEN_BUDGET, 20000),
        call_timeout_seconds=_timeout(env.get("CELLERTZ_SUPERVISOR_CALL_TIMEOUT_SECONDS", "")),
        retries=0,
    )


_GENERATOR_DISPLAY = {
    "bindcraft": "BindCraft",
    "rfdiffusion": "RFdiffusion",
    "mber": "MBER",
    "fixture": "Fixture",
}
_CONFIGURATION_SOURCE = "server_configuration"
_PDL1_SCOPE = "PDL1"
_GENERATOR_LIMITATION = (
    "The server configuration does not name a generation method for this PDL1 request. "
    "BindCraft was not assumed."
)


class DesignRequestSupervisor:
    """One analysis of one saved revision. Records stay free of the API key."""

    def __init__(
        self,
        drafts: DesignDrafting,
        model: ConversationModel,
        limits: SupervisorLimits | None = None,
        configuration: Any = None,
        analyses: Any = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._drafts = drafts
        self._model = model
        self._limits = limits if limits is not None else resolve_supervisor_limits()
        self._configuration = configuration
        self._analyses = analyses
        self._clock = clock if clock is not None else (lambda: datetime.now(UTC))
        self._calls = 0
        self.records: list[dict[str, Any]] = []
        self._saved: list[dict[str, Any]] = []

    def analyze(self, session_id: str, request_id: str, revision: int, message: str) -> dict[str, Any]:
        found = self._drafts.recorded_at(request_id, revision)
        if found is None or found[0].session_id != session_id:
            return _body("draft_not_found", "The saved request does not exist.", self._limits)
        draft, saved = found
        if saved != message:
            return _body("revision_conflict", _MISMATCH, self._limits, request_id, revision, saved)
        if not self._current(session_id, request_id, revision, saved):
            return _body("revision_conflict", _STALE, self._limits, request_id, revision, saved)
        if isinstance(self._model, RecordedFactsModel):
            return _body("supervisor_unavailable", _UNAVAILABLE, self._limits, request_id, revision, saved)
        if self._calls >= self._limits.max_model_calls:
            return _body("supervisor_unavailable", _LIMIT, self._limits, request_id, revision, saved)
        questions = _questions(draft.issues)
        extracted = tuple((item.field, item.question) for item in questions)
        configured_choice, generator_limitation = _configured_generator(draft, self._configuration)
        if configured_choice is not None:
            extracted = tuple(item for item in extracted if item[0] != "generator")
        understood = tuple(
            (field.name, field.value)
            for field in draft.fields
            if field.provenance == "user" and field.value is not None
        )
        facts = (
            ReadOnlyFact(
                "get_design_request",
                "design_request",
                draft.request_id,
                "design_request",
                _separated_facts(saved, draft.revision, understood, extracted, configured_choice, generator_limitation),
            ),
        )
        self._calls += 1
        try:
            completion = _complete(self._model, facts, self._limits)
        except Exception:  # noqa: BLE001 — a provider failure is not an analysis
            return _body("supervisor_unavailable", _FAILED, self._limits, request_id, revision, saved)
        if completion.origin is None or not isinstance(completion.text, str) or not completion.text.strip():
            return _body("supervisor_unavailable", _UNAVAILABLE, self._limits, request_id, revision, saved)
        summary, asked = _split_model_text(completion.text)
        kept, dropped = _without_recorded_gaps(asked, extracted)
        if configured_choice is not None:
            generator_question = next((item.question for item in questions if item.field == "generator"), "")
            kept = tuple(
                question
                for question in kept
                if _normalized(question) != _normalized(generator_question)
            )
        blocked = {field for field, _question in extracted}
        blocked.update(name for name, _value in understood)
        if configured_choice is not None:
            blocked.add(configured_choice["field"])
        selected, dropped, extra = _select_supervisor_questions(kept, dropped, blocked, understood)
        summary = _without_recorded_sentences(summary, extracted)
        current = self._current(session_id, request_id, draft.revision, saved)
        shown = tuple(question for _field, question in selected)
        validation = _validate(summary, shown, completion.usage, self._limits)
        validation["dropped_repeated_gaps"] = list(dropped)
        validation["dropped_extra_questions"] = list(extra)
        usage = completion.usage
        model_call = _model_call(executed=True, model=completion.origin.model, usage=usage)
        record = {
            "request_id": request_id,
            "revision": draft.revision,
            "message": saved,
            "summary": summary,
            "extracted_questions": [_extracted(field, question) for field, question in extracted],
            "supervisor_questions": [
                _supervisor_question(question, field) for field, question in selected
            ],
            "validation": validation,
            "limits": self._limits.to_dict(),
            "provider": completion.origin.provider,
            "model": completion.origin.model,
            "model_call": model_call,
            "input_tokens": None if usage is None else usage.input_tokens,
            "output_tokens": None if usage is None else usage.output_tokens,
            "total_tokens": None if usage is None else usage.total_tokens,
            "configured_choices": [configured_choice] if configured_choice is not None else [],
            "generator_configuration_limitation": generator_limitation,
            "current": current,
        }
        self.records.append(record)
        if validation["status"] != "accepted" or not summary:
            return self._publish(_body(
                "analysis_rejected",
                _REJECTED,
                self._limits,
                request_id,
                draft.revision,
                saved,
                extracted,
                (),
                understood,
                validation,
                recorded=usage is not None,
                provider=completion.origin.provider,
                model=completion.origin.model,
                usage=usage,
                executed=True,
                configured_choices=(configured_choice,) if configured_choice is not None else (),
                generator_limitation=generator_limitation,
                current=current,
            ))
        return self._publish(_body(
            "analyzed",
            summary,
            self._limits,
            request_id,
            draft.revision,
            saved,
            extracted,
            selected,
            understood,
            validation,
            recorded=True,
            provider=completion.origin.provider,
            model=completion.origin.model,
            usage=usage,
            executed=True,
            configured_choices=(configured_choice,) if configured_choice is not None else (),
            generator_limitation=generator_limitation,
            current=current,
        ))

    def analyses_for(self, request_id: str) -> list[dict[str, Any]]:
        """Stored analyses for one request, newest first. Other requests stay out."""

        if self._analyses is not None:
            found = self._analyses.list_for(request_id)
            return found if isinstance(found, list) else []
        return [item for item in reversed(self._saved) if item.get("request_id") == request_id]

    def _publish(self, body: dict[str, Any]) -> dict[str, Any]:
        """Keep a finished analysis. A call that did not run is not stored."""

        if body.get("status") not in {"analyzed", "analysis_rejected"}:
            return body
        moment = self._clock()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        stamped = dict(body)
        stamped["analysis_id"] = f"analysis_{uuid4().hex}"
        stamped["analyzed_at"] = moment.astimezone(UTC).isoformat()
        stamped["execution_authorized"] = False
        stamped["executable"] = False
        self._saved.append(stamped)
        if self._analyses is not None:
            self._analyses.append(dict(stamped))
        return stamped

    def _current(self, session_id: str, request_id: str, revision: int, message: str) -> bool:
        latest = self._drafts.recorded(request_id)
        if latest is None:
            return False
        draft, saved = latest
        return draft.session_id == session_id and draft.revision == revision and saved == message


def handle_list_supervisor_analyses(
    supervisor: DesignRequestSupervisor, path: str
) -> tuple[int, dict[str, Any]]:
    """Read stored analyses for one request. This does not call the model."""

    parsed = urlsplit(path)
    if parsed.path != PATH:
        return 404, _absent_analyses("")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if set(query) != {"request_id"} or len(query["request_id"]) != 1:
        return 400, _body("unsupported_message", "This analysis request was rejected.", supervisor._limits)
    request_id = query["request_id"][0]
    if not request_id.strip():
        return 400, _body("unsupported_message", "This analysis request was rejected.", supervisor._limits)
    analyses = [
        item
        for item in supervisor.analyses_for(request_id)
        if isinstance(item, dict) and item.get("request_id") == request_id
    ]
    return 200, {
        "status": "analyses_recorded",
        "request_id": request_id,
        "analyses": analyses,
        "execution_authorized": False,
        "executable": False,
    }


def _absent_analyses(request_id: str) -> dict[str, Any]:
    return {
        "status": "analyses_recorded",
        "request_id": request_id,
        "analyses": [],
        "execution_authorized": False,
        "executable": False,
    }


def handle_design_supervisor(supervisor: DesignRequestSupervisor, body: bytes) -> tuple[int, dict[str, Any]]:
    """Analyze the stated revision. This does not revise the request or start work."""

    limits = supervisor._limits
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return 400, _body("unsupported_message", "This analysis request was rejected.", limits)
    if not isinstance(payload, dict) or set(payload) != _BODY_KEYS:
        return 400, _body("unsupported_message", "This analysis request was rejected.", limits)
    session_id = payload.get("session_id")
    request_id = payload.get("request_id")
    revision = payload.get("revision")
    message = payload.get("message")
    if not isinstance(session_id, str) or not session_id.strip():
        return 400, _body("unsupported_message", "This analysis request was rejected.", limits)
    if not isinstance(request_id, str) or not request_id.strip():
        return 400, _body("unsupported_message", "This analysis request was rejected.", limits)
    if type(revision) is not int or isinstance(revision, bool) or revision < 1:
        return 400, _body("unsupported_message", "This analysis request was rejected.", limits)
    if not isinstance(message, str) or message == "":
        return 400, _body("unsupported_message", "This analysis request was rejected.", limits)
    result = supervisor.analyze(session_id, request_id, revision, message)
    if result["status"] == "draft_not_found":
        status = 404
    elif result["status"] == "revision_conflict":
        status = 409
    else:
        status = 200
    return status, result


def _complete(model: ConversationModel, facts: tuple[ReadOnlyFact, ...], limits: SupervisorLimits) -> Any:
    parameters = inspect.signature(model.complete).parameters
    arguments: dict[str, Any] = {"facts": facts, "question": _QUESTION}
    if "instruction" in parameters:
        arguments["instruction"] = _QUESTION
    if "max_tokens" in parameters:
        arguments["max_tokens"] = limits.max_output_tokens
    if "timeout" in parameters:
        arguments["timeout"] = limits.call_timeout_seconds
    return model.complete(**arguments)


def _split_model_text(text: str) -> tuple[str, tuple[str, ...]]:
    lines = text.splitlines()
    split_at = None
    for index, line in enumerate(lines):
        if line.strip().lower().startswith("questions:"):
            split_at = index
            break
    if split_at is None:
        return text.strip(), ()
    summary = "\n".join(lines[:split_at]).strip()
    inline = lines[split_at].split(":", 1)[1].strip()
    asked: list[str] = []
    if inline and inline.lower() != "none":
        asked.append(inline)
    for line in lines[split_at + 1 :]:
        cleaned = line.strip().lstrip("-").strip()
        if cleaned and cleaned.lower() != "none":
            asked.append(cleaned)
    return summary, tuple(asked)


def _separated_facts(
    saved: str,
    revision: int,
    understood: tuple[tuple[str, object], ...],
    extracted: tuple[tuple[str, str], ...],
    configured_choice: dict[str, str] | None,
    generator_limitation: str | None,
) -> tuple[tuple[str, str], ...]:
    researcher = {
        "source": "researcher",
        "text": saved,
        "fields": [{"name": name, "value": value} for name, value in understood],
    }
    server = {
        "source": "server_configuration",
        "configured_generator": configured_choice,
        "limitation": generator_limitation,
    }
    missing = {
        "source": "missing_or_ambiguous",
        "recorded_gaps": [question for _, question in extracted],
    }
    return (
        ("message", saved),
        ("revision", str(revision)),
        ("researcher_information", json.dumps(researcher, sort_keys=True)),
        ("server_configuration", json.dumps(server, sort_keys=True)),
        ("missing_or_ambiguous", json.dumps(missing, sort_keys=True)),
    )


_FIELD_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("n_candidates", ("n_candidates", "candidate")),
    ("length_min", ("length_min",)),
    ("length_max", ("length_max",)),
    ("filter_policy", ("filter_policy", "filter policy")),
    ("generator", ("generator", "generation method")),
    ("objective", ("design objective", "objective")),
    ("seed", ("integer seed", "seed")),
    ("target_id", ("target_id", "scientific target", "which target")),
)


def _cue_matches(normalized: str, cue: str) -> bool:
    if " " in cue or "_" in cue or cue == "candidate":
        return cue in normalized
    return f" {cue} " in f" {normalized} "


def _cued_fields(normalized: str) -> tuple[str, ...]:
    return tuple(
        name for name, cues in _FIELD_CUES if any(_cue_matches(normalized, cue) for cue in cues)
    )


def _known_question_field(question: str) -> tuple[str | None, str]:
    """Identity of a known field. A prefix wins; otherwise one unambiguous cue."""

    stripped = question.strip()
    head, separator, tail = stripped.partition(":")
    named = head.strip().casefold()
    if separator and named in _QUESTIONS and tail.strip():
        return named, tail.strip()
    matched = _cued_fields(_normalized(stripped))
    if len(matched) == 1:
        return matched[0], stripped
    return None, stripped


def _select_supervisor_questions(
    questions: tuple[str, ...],
    dropped: tuple[str, ...],
    blocked: set[str],
    understood: tuple[tuple[str, object], ...],
) -> tuple[tuple[tuple[str | None, str], ...], tuple[str, ...], tuple[str, ...]]:
    answered = {
        _normalized(_QUESTIONS[name])
        for name, _value in understood
        if name in _QUESTIONS
    }
    selected: list[tuple[str | None, str]] = []
    repeated = list(dropped)
    seen: set[str] = set()
    for question in questions:
        field, text = _known_question_field(question)
        occupied = set(blocked)
        occupied.update(seen)
        same_field = (field is not None and field in occupied) or any(
            name in occupied for name in _cued_fields(_normalized(question))
        )
        if same_field or _normalized(question) in answered:
            repeated.append(question)
            continue
        if field is not None:
            seen.add(field)
        selected.append((field, text))
    shown = tuple(selected[:_MAX_SUPERVISOR_QUESTIONS])
    extra = tuple(text for _field, text in selected[_MAX_SUPERVISOR_QUESTIONS:])
    return shown, tuple(repeated), extra


def _without_recorded_gaps(
    asked: tuple[str, ...],
    extracted: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    recorded = {_normalized(question) for _field, question in extracted}
    kept: list[str] = []
    dropped: list[str] = []
    for question in asked:
        if _normalized(question) in recorded:
            dropped.append(question)
        else:
            kept.append(question)
    return tuple(kept), tuple(dropped)


def _without_recorded_sentences(summary: str, extracted: tuple[tuple[str, str], ...]) -> str:
    recorded = {_normalized(question) for _field, question in extracted}
    kept = [line for line in summary.splitlines() if _normalized(line) not in recorded]
    return "\n".join(kept).strip()


def _validate(summary: str, questions: tuple[str, ...], usage: Any, limits: SupervisorLimits) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    if not summary:
        findings.append({"code": "empty_summary", "value": "summary"})
    blob = f"{summary}\n" + "\n".join(questions)
    lowered = blob.casefold()
    for phrase in _EXECUTED:
        if phrase in lowered:
            findings.append({"code": "executed_decision", "value": phrase})
    if usage is None or getattr(usage, "total_tokens", None) is None:
        findings.append({"code": "usage_not_reported", "value": "total_tokens"})
    elif usage.total_tokens > limits.token_budget:
        findings.append({"code": "token_budget_exceeded", "value": str(usage.total_tokens)})
    return {
        "status": "rejected" if findings else "accepted",
        "findings": findings,
        "limitations": list(_LIMITATIONS),
    }


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def _model_call(*, executed: bool, model: str | None = None, usage: Any = None) -> dict[str, Any]:
    """Show the provider's report. A call that did not run is never recorded as zero tokens."""

    if not executed:
        return {
            "executed": False,
            "model": _NOT_EXECUTED,
            "input_tokens": _NOT_EXECUTED,
            "output_tokens": _NOT_EXECUTED,
            "total_tokens": _NOT_EXECUTED,
        }
    name = model.strip() if isinstance(model, str) and model.strip() else _NOT_INFORMED
    return {
        "executed": True,
        "model": name,
        "input_tokens": _reported_token(None if usage is None else usage.input_tokens),
        "output_tokens": _reported_token(None if usage is None else usage.output_tokens),
        "total_tokens": _reported_token(None if usage is None else usage.total_tokens),
    }


def _reported_token(value: object) -> int | str:
    if type(value) is int and not isinstance(value, bool):
        return value
    return _NOT_INFORMED


def _extracted(field: str, question: str) -> dict[str, str]:
    return {"field": field, "question": question, "source": "recorded"}


def _supervisor_question(question: str, field: str | None = None) -> dict[str, Any]:
    return {"question": question, "source": "supervisor", "field": field}


def _body(
    status: str,
    summary: str,
    limits: SupervisorLimits,
    request_id: str = "",
    revision: int | None = None,
    message: str = "",
    extracted: tuple[tuple[str, str], ...] = (),
    supervisor_questions: tuple[tuple[str | None, str], ...] = (),
    understood: tuple[tuple[str, object], ...] = (),
    validation: dict[str, Any] | None = None,
    *,
    recorded: bool = False,
    provider: str | None = None,
    model: str | None = None,
    usage: Any = None,
    executed: bool = False,
    configured_choices: tuple[dict[str, str], ...] = (),
    generator_limitation: str | None = None,
    current: bool = False,
) -> dict[str, Any]:
    call = _model_call(executed=executed, model=model, usage=usage)
    reported_input = None if usage is None else usage.input_tokens
    reported_output = None if usage is None else usage.output_tokens
    reported_total = None if usage is None else usage.total_tokens
    return {
        "status": status,
        "agent": AGENT,
        "request_id": request_id,
        "revision": revision,
        "message": message,
        "summary": summary,
        "extracted_questions": [_extracted(field, question) for field, question in extracted],
        "supervisor_questions": [
            _supervisor_question(question, field) for field, question in supervisor_questions
        ],
        "understood": [{"name": name, "value": value} for name, value in understood],
        "validation": validation
        or {"status": "not_run", "findings": [], "limitations": list(_LIMITATIONS)},
        "limits": limits.to_dict(),
        "execution_authorized": False,
        "executable": False,
        "model_call": call,
        "instrumentation": {
            "interaction_id": f"supervisor_{uuid4().hex}",
            "recorded": recorded,
            "observation": "recorded" if recorded else "no_model_usage",
            "provider": provider if executed else None,
            "model": model if executed else None,
            "input_tokens": reported_input if executed else None,
            "output_tokens": reported_output if executed else None,
            "total_tokens": reported_total if executed else None,
        },
        "configured_choices": [dict(choice) for choice in configured_choices],
        "generator_configuration_limitation": generator_limitation,
        "current": current,
    }


def _configured_generator(
    draft: Any,
    configuration: Any,
) -> tuple[dict[str, str] | None, str | None]:
    """Resolve only a missing generator for a saved PDL1 request. The draft stays unchanged."""

    fields = {field.name: field for field in draft.fields}
    target = fields.get("target_id")
    generator = fields.get("generator")
    if target is None or target.value != _PDL1_SCOPE or target.provenance != "user":
        return None, None
    if generator is not None and generator.value is not None and generator.provenance == "user":
        return None, None
    read = getattr(configuration, "read", None)
    reply = None
    if callable(read):
        try:
            reply = read()
        except Exception:  # noqa: BLE001 — a failed read is not a generator choice
            reply = None
    tool = getattr(reply, "tool_name", None)
    scope = getattr(reply, "target_scope", None)
    if tool in GENERATORS and scope == _PDL1_SCOPE and tool in _GENERATOR_DISPLAY:
        return (
            {
                "field": "generator",
                "value": tool,
                "text": f"Generation method: {_GENERATOR_DISPLAY[tool]}",
                "source": _CONFIGURATION_SOURCE,
            },
            None,
        )
    return None, _GENERATOR_LIMITATION


def _positive_int(value: str, default: int, ceiling: int) -> int:
    text = value.strip()
    if not text:
        return default
    if not text.isascii() or not text.isdigit():
        return default
    number = int(text)
    if number < 1 or number > ceiling:
        return default
    return number


def _timeout(value: str) -> float:
    text = value.strip()
    if not text:
        return DEFAULT_CALL_TIMEOUT_SECONDS
    try:
        number = float(text)
    except ValueError:
        return DEFAULT_CALL_TIMEOUT_SECONDS
    if number <= 0 or number > 120:
        return DEFAULT_CALL_TIMEOUT_SECONDS
    return number
