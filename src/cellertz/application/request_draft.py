"""Conversational drafts of a ProteinDesignRequest.

A draft is not approval and not execution. A structurally complete
request stays unauthorized. This module has no execution port and does
not write scientific memory.

The patchable fields are the GenerationRequest inputs already required
by the Supervisor: target_id, objective, generator, n_candidates, and
the documented constraint keys length_min and length_max. An optional
integer constraint ``seed`` (0 through 4294967295) and an optional
``filter_policy`` of ``no_filters`` are recorded only when a revision
sets them. They are omitted when absent, so an older revision digest
does not change. Ranking stays the documented raw ipSAE policy and
cannot be patched.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from cellertz.application.contracts import (
    ApplicationContractError,
    ModelUsageEvent,
    WorkspaceSession,
    _aware,
    _text,
)
from cellertz.application.conversation import (
    ConversationInstrumentation,
    ListUsageRecorder,
    ModelUsageReport,
    UsageRecorder,
)
from cellertz.application.pdl1_selection import Pdl1InputSelection
from cellertz.application.request_draft_store import DraftRevisionConflict

DRAFT_STATUSES = frozenset(
    {
        "draft_created",
        "draft_updated",
        "clarification_needed",
        "request_complete",
        "unsupported_message",
        "forbidden_action",
        "confirmation_not_available_until_phase_6d",
        "session_not_found",
        "draft_not_found",
        "revision_conflict",
        "invalid_patch",
        "adapter_failure",
    }
)
PROVENANCE = frozenset(
    {
        "user",
        "preserved",
        "documented_default",
        "absent",
        "ambiguous",
        "rejected",
    }
)
PATCHABLE_ORDER = (
    "target_id",
    "objective",
    "generator",
    "n_candidates",
    "length_min",
    "length_max",
    "seed",
    "filter_policy",
)
PATCHABLE = frozenset(PATCHABLE_ORDER)
REQUIRED = ("target_id", "objective", "generator", "n_candidates")
OBJECTIVES = frozenset({"binder_design"})
GENERATORS = frozenset({"bindcraft", "rfdiffusion", "mber", "fixture"})
LOCKED = frozenset(
    {
        "execution_authorized",
        "executable",
        "session_id",
        "request_id",
        "revision",
        "revision_id",
        "provenance",
        "ranking_score",
        "rank",
        "composite_score",
        "boltz_score",
        "is_probability",
        "recommended_as_hard_filter",
        "ranking_signal",
        "boltz_role",
    }
)
DOCUMENTED = (
    ("ranking_signal", "raw_ipsae"),
    ("boltz_role", "auxiliary_evidence"),
)
_AGENT_ID = "design_request_draft"
_STAGE = "request_draft"
_FORBIDDEN_TEXT = "This action is blocked. A draft is not approval and not execution."
_CONFIRM_TEXT = "Confirmation is not available until Phase 6D. The draft was kept."
_UNSUPPORTED_TEXT = "This message is not a protein design request draft."
_SESSION_TEXT = "The session does not exist."
_DRAFT_TEXT = "The draft revision does not exist."
_CONFLICT_TEXT = "The draft has a newer revision. The patch was not applied."
_ADAPTER_TEXT = "The conversational adapter failed. No scientific action was taken."
_INVALID_TEXT = "The proposed patch was rejected. The draft was not revised."
_CLARIFY_TEXT = "More information is required before this request is complete."
_CREATED_TEXT = "A draft revision was created. It is not authorized and not executable."
_UPDATED_TEXT = "The draft was revised. It is not authorized and not executable."
_COMPLETE_TEXT = "The request is structurally complete. It is not authorized and not executable."
_QUESTIONS = {
    "target_id": "Which scientific target should this request use?",
    "objective": "What design objective should be used? The documented objective is binder_design.",
    "generator": (
        "Which generator should be used? Documented adapters are "
        "bindcraft, rfdiffusion, mber, and fixture."
    ),
    "n_candidates": "How many candidates should be requested?",
    "length_min": "What integer length_min should be used?",
    "length_max": "What integer length_max should be used?",
    "seed": "What integer seed from 0 through 4294967295 should be used?",
    "filter_policy": "Which filter policy should be used? The closed value is no_filters.",
}
_OPTIONAL_RECORDED = frozenset({"seed", "filter_policy"})
_INTEGER_FIELDS = frozenset({"n_candidates", "length_min", "length_max", "seed"})
_SEED_MAX = 4294967295
FILTER_POLICIES = frozenset({"no_filters"})
_CONSTRAINT_KEYS = frozenset({"length_min", "length_max", "seed"})

# ponytail: phrase list, not a parser. "executed" is a past read;
# naming bindcraft as a generator is not an order to run it.
_BLOCKED_PHRASES = (
    "execute",
    "executar",
    "approve",
    "aprovar",
    "start the run",
    "start a run",
    "start a fixture",
    "start the fixture",
    "inicie o run",
    "inicie a execucao",
    "iniciar o run",
    "iniciar a execucao",
    "resume",
    "retomar",
    "run bindcraft",
    "start bindcraft",
    "execute bindcraft",
    "executar bindcraft",
    "call bindcraft",
    "chamar bindcraft",
    "run boltz",
    "execute boltz",
    "executar boltz",
    "rodar boltz",
    "run the supervisor",
    "start the supervisor",
    "chamar o supervisor",
    "promote",
    "ingest",
    "ingerir",
    "second brain",
    "authorize",
    "autorize",
    "autoriz",
    "composite score",
    "score composto",
    "hard filter",
    "filtro duro",
    "alterar o ranking",
    "alterar ranking",
    "change the ranking",
    "priorizar boltz",
    "priorizando boltz",
)
_DRAFT_TOKENS = (
    "design",
    "desenhar",
    "desenho",
    "binder",
    "draft",
    "request",
    "target",
    "alvo",
    "generator",
    "gerador",
    "candidate",
    "candidato",
    "constraint",
    "comprimento",
    "length",
    "quero",
    "want",
    "revis",
    "atualiz",
    "pdl1",
    "bcma",
    "ipsae",
    "objective",
    "objetivo",
)


_CONFIRMATION_ONLY = frozenset(
    {
        "confirmo",
        "confirme",
        "confirmar",
        "confirm",
        "confirmed",
        "i confirm",
        "confirm this",
        "confirm the request",
        "confirm the draft",
        "looks good",
    }
)


def _isolated_confirmation(message: str) -> bool:
    folded = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in message.casefold())
    return " ".join(folded.split()) in _CONFIRMATION_ONLY


def _blocked(message: str) -> bool:
    folded = " ".join(message.casefold().split())
    past = folded.replace("executed", " ").replace("executado", " ")
    for innocent in ("unauthorized", "not authorized", "não autorizado", "nao autorizado"):
        past = past.replace(innocent, " ")
    return any(phrase in past for phrase in _BLOCKED_PHRASES)


def _draft_intent(message: str) -> bool:
    folded = message.casefold()
    return any(token in folded for token in _DRAFT_TOKENS)


@dataclass(frozen=True)
class RequestField:
    name: str
    value: str | int | None
    provenance: str

    def __post_init__(self) -> None:
        _text(self.name, "invalid_field")
        if self.provenance not in PROVENANCE:
            raise ApplicationContractError("invalid_provenance")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "provenance": self.provenance}


@dataclass(frozen=True)
class ValidationIssue:
    field: str
    code: str

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "code": self.code}


@dataclass(frozen=True)
class Clarification:
    field: str
    question: str

    def to_dict(self) -> dict[str, str]:
        return {"field": self.field, "question": self.question}


@dataclass(frozen=True)
class ProteinDesignRequestDraft:
    """Incomplete or in review. Never authorized."""

    request_id: str
    session_id: str
    revision: int
    created_at: datetime
    updated_at: datetime
    completeness: str
    fields: tuple[RequestField, ...]
    issues: tuple[ValidationIssue, ...]
    execution_authorized: bool = False
    executable: bool = False
    input_selection: Pdl1InputSelection | None = None

    def __post_init__(self) -> None:
        _text(self.request_id, "invalid_request_id")
        _text(self.session_id, "invalid_session_id")
        if (
            self.input_selection is not None
            and type(self.input_selection) is not Pdl1InputSelection
        ):
            raise ApplicationContractError("pdl1_input_selection_invalid")
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ApplicationContractError("invalid_revision")
        _aware(self.created_at)
        _aware(self.updated_at)
        if self.completeness not in {"incomplete", "complete"}:
            raise ApplicationContractError("invalid_completeness")
        if self.execution_authorized or self.executable:
            raise ApplicationContractError("request_is_not_authorized")

    def to_dict(self) -> dict[str, Any]:
        document = {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "revision": self.revision,
            "created_at": _aware(self.created_at).isoformat(),
            "updated_at": _aware(self.updated_at).isoformat(),
            "completeness": self.completeness,
            "execution_authorized": False,
            "executable": False,
            "fields": [field.to_dict() for field in self.fields],
            "issues": [issue.to_dict() for issue in self.issues],
        }
        if self.input_selection is not None:
            document["input_selection"] = self.input_selection.to_dict()
        return document


@dataclass(frozen=True)
class ProteinDesignRequest:
    """Structurally complete GenerationRequest inputs. Not authorized."""

    request_id: str
    session_id: str
    revision: int
    target_id: str
    objective: str
    generator: str
    n_candidates: int
    constraints: tuple[tuple[str, int], ...]
    filter_policy: str | None = None
    execution_authorized: bool = False
    executable: bool = False

    def __post_init__(self) -> None:
        _text(self.request_id, "invalid_request_id")
        _text(self.session_id, "invalid_session_id")
        _text(self.target_id, "invalid_target_id")
        if self.objective not in OBJECTIVES:
            raise ApplicationContractError("invalid_objective")
        if self.generator not in GENERATORS:
            raise ApplicationContractError("invalid_generator")
        if (
            isinstance(self.n_candidates, bool)
            or not isinstance(self.n_candidates, int)
            or self.n_candidates < 1
        ):
            raise ApplicationContractError("invalid_n_candidates")
        _validate_constraints(self.constraints)
        if self.filter_policy is not None and (
            type(self.filter_policy) is not str or self.filter_policy not in FILTER_POLICIES
        ):
            raise ApplicationContractError("invalid_filter_policy")
        if self.execution_authorized or self.executable:
            raise ApplicationContractError("request_is_not_authorized")

    def generation_payload(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "objective": self.objective,
            "generator": self.generator,
            "n_candidates": self.n_candidates,
            "constraints": dict(self.constraints),
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self.generation_payload()
        payload.update(
            {
                "request_id": self.request_id,
                "session_id": self.session_id,
                "revision": self.revision,
                "completeness": "complete",
                "execution_authorized": False,
                "executable": False,
            }
        )
        if self.filter_policy is not None:
            payload["filter_policy"] = self.filter_policy
        return payload


@dataclass(frozen=True)
class FieldProposal:
    name: str
    value: object = None
    ambiguous: bool = False


@dataclass(frozen=True)
class DraftPatch:
    proposals: tuple[FieldProposal, ...] = ()
    usage: ModelUsageReport | None = None


class DraftExtractor(Protocol):
    def extract(
        self,
        *,
        message: str,
        draft: ProteinDesignRequestDraft | None,
    ) -> DraftPatch: ...


@dataclass(frozen=True)
class DesignDraftCommand:
    session_id: str
    message: str
    request_id: str | None = None
    revision: int | None = None
    clarifications: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        _text(self.session_id, "invalid_session_id")
        _text(self.message, "empty_message")
        if self.request_id is not None:
            _text(self.request_id, "invalid_request_id")
        if self.revision is None:
            return
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise ApplicationContractError("invalid_revision")


@dataclass(frozen=True)
class DesignDraftReply:
    status: str
    text: str
    session_id: str
    draft: ProteinDesignRequestDraft | None
    request: ProteinDesignRequest | None
    questions: tuple[Clarification, ...]
    issues: tuple[ValidationIssue, ...]
    changed_fields: tuple[str, ...]
    instrumentation: ConversationInstrumentation
    execution_authorized: bool = False
    executable: bool = False

    def __post_init__(self) -> None:
        if self.status not in DRAFT_STATUSES:
            raise ApplicationContractError("invalid_draft_reply_status")
        _text(self.text, "empty_message")
        if self.execution_authorized or self.executable:
            raise ApplicationContractError("request_is_not_authorized")
        if self.status == "request_complete" and self.request is None:
            raise ApplicationContractError("complete_request_missing")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "text": self.text,
            "session_id": self.session_id,
            "execution_authorized": False,
            "executable": False,
            "changed_fields": list(self.changed_fields),
            "questions": [item.to_dict() for item in self.questions],
            "issues": [item.to_dict() for item in self.issues],
            "draft": None if self.draft is None else self.draft.to_dict(),
            "request": None if self.request is None else self.request.to_dict(),
            "instrumentation": self.instrumentation.to_dict(),
        }


def _unused(interaction_id: str) -> ConversationInstrumentation:
    return ConversationInstrumentation(
        interaction_id=interaction_id,
        recorded=False,
        event=None,
        total_tokens=None,
        observation="no_model_usage",
    )


def _field_map(draft: ProteinDesignRequestDraft | None) -> dict[str, RequestField]:
    if draft is None:
        return {}
    return {field.name: field for field in draft.fields}


def _validate_constraints(pairs: object) -> None:
    if type(pairs) is not tuple:
        raise ApplicationContractError("invalid_constraints")
    seen: set[str] = set()
    values: dict[str, int] = {}
    for item in pairs:
        if type(item) is not tuple or len(item) != 2:
            raise ApplicationContractError("invalid_constraints")
        name, value = item
        if type(name) is not str or name not in _CONSTRAINT_KEYS or name in seen:
            raise ApplicationContractError("invalid_constraints")
        if type(value) is not int or isinstance(value, bool):
            raise ApplicationContractError("invalid_constraints")
        if name == "seed":
            if value < 0 or value > _SEED_MAX:
                raise ApplicationContractError("invalid_constraints")
        elif value < 1:
            raise ApplicationContractError("invalid_constraints")
        seen.add(name)
        values[name] = value
    if ("length_min" in seen) != ("length_max" in seen):
        raise ApplicationContractError("invalid_constraints")
    if "length_min" in seen and values["length_min"] > values["length_max"]:
        raise ApplicationContractError("invalid_constraints")


def _accepted_value(name: str, value: object) -> str | int:
    if name == "filter_policy":
        if type(value) is not str or value not in FILTER_POLICIES:
            raise ApplicationContractError("invalid_value")
        return value
    if name == "seed":
        if type(value) is not int or isinstance(value, bool) or value < 0 or value > _SEED_MAX:
            raise ApplicationContractError("invalid_value")
        return value
    if name in {"target_id", "objective", "generator"}:
        if not isinstance(value, str) or not value.strip():
            raise ApplicationContractError("invalid_value")
        text = value.strip()
        if name == "objective" and text not in OBJECTIVES:
            raise ApplicationContractError("invalid_value")
        if name == "generator" and text not in GENERATORS:
            raise ApplicationContractError("invalid_value")
        return text
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ApplicationContractError("invalid_value")
    return value


def _apply(
    previous: ProteinDesignRequestDraft | None,
    proposals: tuple[FieldProposal, ...],
) -> tuple[dict[str, RequestField], tuple[ValidationIssue, ...], tuple[str, ...]]:
    current = _field_map(previous)
    issues: list[ValidationIssue] = []
    accepted: dict[str, str | int] = {}
    ambiguous: set[str] = set()
    for proposal in proposals:
        name = proposal.name
        if name in LOCKED:
            issues.append(ValidationIssue(name, "rejected"))
            continue
        if name not in PATCHABLE:
            issues.append(ValidationIssue(str(name), "unknown"))
            continue
        if proposal.ambiguous:
            stored = current.get(name)
            if stored is None or stored.value is None:
                ambiguous.add(name)
                issues.append(ValidationIssue(name, "ambiguous"))
            continue
        try:
            accepted[name] = _accepted_value(name, proposal.value)
        except ApplicationContractError:
            issues.append(ValidationIssue(name, "invalid"))
    low = accepted.get("length_min", _stored_int(current.get("length_min")))
    high = accepted.get("length_max", _stored_int(current.get("length_max")))
    if low is not None and high is not None and low > high:
        for name in ("length_min", "length_max"):
            if name in accepted:
                accepted.pop(name)
                issues.append(ValidationIssue(name, "invalid"))
    changed: list[str] = []
    merged: dict[str, RequestField] = {}
    for name in PATCHABLE_ORDER:
        if name in accepted:
            previous_field = current.get(name)
            if previous_field is None or previous_field.value != accepted[name]:
                changed.append(name)
            merged[name] = RequestField(name, accepted[name], "user")
            continue
        if name in ambiguous:
            if name not in _OPTIONAL_RECORDED:
                merged[name] = RequestField(name, None, "ambiguous")
            continue
        previous_field = current.get(name)
        if previous_field is not None and previous_field.value is not None:
            merged[name] = RequestField(name, previous_field.value, "preserved")
        elif name not in _OPTIONAL_RECORDED:
            merged[name] = RequestField(name, None, "absent")
    for name, value in DOCUMENTED:
        merged[name] = RequestField(name, value, "documented_default")
    return merged, tuple(issues), tuple(changed)


def _stored_int(field: RequestField | None) -> int | None:
    if field is None or not isinstance(field.value, int) or isinstance(field.value, bool):
        return None
    return field.value


def _structural_issues(fields: Mapping[str, RequestField]) -> tuple[ValidationIssue, ...]:
    issues = [
        ValidationIssue(name, "missing")
        for name in REQUIRED
        if fields[name].provenance in {"absent", "ambiguous"}
    ]
    low = fields["length_min"]
    high = fields["length_max"]
    if low.value is not None and high.provenance == "absent":
        issues.append(ValidationIssue("length_max", "missing"))
    if high.value is not None and low.provenance == "absent":
        issues.append(ValidationIssue("length_min", "missing"))
    return tuple(issues)


def _questions(issues: tuple[ValidationIssue, ...]) -> tuple[Clarification, ...]:
    seen: set[str] = set()
    questions: list[Clarification] = []
    for issue in issues:
        if issue.code not in {"missing", "ambiguous", "invalid"} or issue.field in seen:
            continue
        if issue.field not in _QUESTIONS:
            continue
        seen.add(issue.field)
        if issue.code == "missing":
            text = _QUESTIONS[issue.field]
        elif issue.code == "ambiguous":
            text = f"The value for {issue.field} is ambiguous. {_QUESTIONS[issue.field]}"
        else:
            text = f"The value for {issue.field} was rejected. {_QUESTIONS[issue.field]}"
        questions.append(Clarification(issue.field, text))
    return tuple(questions)


def _ordered(fields: Mapping[str, RequestField]) -> tuple[RequestField, ...]:
    names = (
        "target_id",
        "objective",
        "generator",
        "n_candidates",
        "length_min",
        "length_max",
        "ranking_signal",
        "boltz_role",
    )
    recorded = tuple(fields[name] for name in ("seed", "filter_policy") if name in fields)
    return tuple(fields[name] for name in names) + recorded


def _request_from(draft: ProteinDesignRequestDraft) -> ProteinDesignRequest | None:
    if draft.completeness != "complete":
        return None
    values = _field_map(draft)
    constraints = tuple(
        (name, values[name].value)
        for name in ("length_min", "length_max", "seed")
        if name in values
        and type(values[name].value) is int
        and not isinstance(values[name].value, bool)
    )
    recorded_filter = values["filter_policy"].value if "filter_policy" in values else None
    filter_policy = recorded_filter if type(recorded_filter) is str else None
    return ProteinDesignRequest(
        request_id=draft.request_id,
        session_id=draft.session_id,
        revision=draft.revision,
        target_id=str(values["target_id"].value),
        objective=str(values["objective"].value),
        generator=str(values["generator"].value),
        n_candidates=int(values["n_candidates"].value),  # type: ignore[arg-type]
        constraints=constraints,  # type: ignore[arg-type]
        filter_policy=filter_policy,
    )


def _token_char(char: str) -> bool:
    return char.isalnum() or char in "_-"


def _spoken_tokens(folded: str) -> list[str]:
    """Drop punctuation stuck to a word. Underscores and hyphens stay inside the token."""

    tokens: list[str] = []
    for raw in folded.split():
        start = 0
        end = len(raw)
        while start < end and not _token_char(raw[start]):
            start += 1
        while end > start and not _token_char(raw[end - 1]):
            end -= 1
        if start < end:
            tokens.append(raw[start:end])
    return tokens


def _trailing_count(message: str) -> int | None:
    """A final line that is only an integer answers the candidate-count question."""

    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not lines:
        return None
    count = _canonical_int(lines[-1])
    if count is None or count < 1:
        return None
    return count


def _clarification_proposals(items: tuple[tuple[str, object], ...]) -> tuple[FieldProposal, ...]:
    """Turn explicit answers into proposals. The saved objective text stays unchanged."""

    proposals: list[FieldProposal] = []
    for name, raw in items:
        value: object = raw
        if name in _INTEGER_FIELDS and isinstance(raw, str):
            number = _canonical_int(raw.strip())
            value = raw if number is None else number
        elif isinstance(raw, str):
            value = raw.strip()
        proposals.append(FieldProposal(name, value))
    return tuple(proposals)


def _canonical_int(token: str) -> int | None:
    if not token or len(token) > 10 or any(char < "0" or char > "9" for char in token):
        return None
    if len(token) > 1 and token.startswith("0"):
        return None
    return int(token)


class DeterministicDraftExtractor:
    """Not an LLM. Maps a few documented phrases onto a patch."""

    def extract(
        self,
        *,
        message: str,
        draft: ProteinDesignRequestDraft | None,
    ) -> DraftPatch:
        del draft
        folded = " ".join(message.casefold().split())
        proposals: list[FieldProposal] = []
        if "pdl1" in folded:
            proposals.append(FieldProposal("target_id", "PDL1"))
        elif "bcma" in folded:
            proposals.append(FieldProposal("target_id", "BCMA"))
        if any(token in folded for token in ("binder", "desenhar", "design")):
            proposals.append(FieldProposal("objective", "binder_design"))
        parts = _spoken_tokens(folded)
        for name in ("bindcraft", "rfdiffusion", "mber", "fixture"):
            if name in parts:
                proposals.append(FieldProposal("generator", name))
                break
        if "candidates" in parts:
            index = parts.index("candidates")
            if index > 0 and _canonical_int(parts[index - 1]) is not None:
                proposals.append(FieldProposal("n_candidates", int(parts[index - 1])))
        elif (count := _trailing_count(message)) is not None:
            proposals.append(FieldProposal("n_candidates", count))
        if "length" in parts:
            index = parts.index("length")
            if index + 2 < len(parts):
                low = _canonical_int(parts[index + 1])
                high = _canonical_int(parts[index + 2])
                if low is not None and high is not None and low >= 1 and high >= 1:
                    proposals.append(FieldProposal("length_min", low))
                    proposals.append(FieldProposal("length_max", high))
        if "seed" in parts:
            index = parts.index("seed")
            if index + 1 < len(parts):
                seed = _canonical_int(parts[index + 1])
                if seed is not None:
                    proposals.append(FieldProposal("seed", seed))
        if "no_filters" in parts:
            proposals.append(FieldProposal("filter_policy", "no_filters"))
        return DraftPatch(tuple(proposals))


def _stored_draft(document: Mapping[str, Any]) -> tuple[ProteinDesignRequestDraft, str]:
    """Rebuild one saved revision. Absent fields stay absent."""

    raw = document.get("draft")
    if set(document) != {"message", "draft"} or not isinstance(raw, dict):
        raise ApplicationContractError("invalid_stored_draft")
    message = _text(document.get("message"), "empty_message")
    if raw.get("execution_authorized") is not False or raw.get("executable") is not False:
        raise ApplicationContractError("request_is_not_authorized")
    raw_fields = raw.get("fields")
    raw_issues = raw.get("issues")
    if not isinstance(raw_fields, list) or not isinstance(raw_issues, list):
        raise ApplicationContractError("invalid_stored_draft")
    fields = tuple(
        RequestField(item["name"], item["value"], item["provenance"])
        for item in raw_fields
        if isinstance(item, dict)
    )
    if len(fields) != len(raw_fields):
        raise ApplicationContractError("invalid_stored_draft")
    issues = tuple(
        ValidationIssue(item["field"], item["code"])
        for item in raw_issues
        if isinstance(item, dict)
    )
    if len(issues) != len(raw_issues):
        raise ApplicationContractError("invalid_stored_draft")
    selection = None
    selected = raw.get("input_selection")
    if selected is not None:
        if not isinstance(selected, dict):
            raise ApplicationContractError("invalid_stored_draft")
        selection = Pdl1InputSelection(
            manifest_digest=selected["manifest_digest"],
            target_structure_sha256=selected["target_structure_sha256"],
            settings_sha256=selected["settings_sha256"],
            advanced_profile_sha256=selected["advanced_profile_sha256"],
            schema=selected["schema"],
        )
    try:
        created_at = datetime.fromisoformat(raw["created_at"])
        updated_at = datetime.fromisoformat(raw["updated_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ApplicationContractError("invalid_stored_draft") from exc
    draft = ProteinDesignRequestDraft(
        request_id=raw["request_id"],
        session_id=raw["session_id"],
        revision=raw["revision"],
        created_at=created_at,
        updated_at=updated_at,
        completeness=raw["completeness"],
        fields=fields,
        issues=issues,
        input_selection=selection,
    )
    return draft, message


class DesignDrafting:
    """Build immutable draft revisions. Cannot approve or execute."""

    def __init__(
        self,
        sessions: Mapping[str, WorkspaceSession],
        extractor: DraftExtractor,
        recorder: UsageRecorder | None = None,
        clock: Callable[[], datetime] | None = None,
        interaction_ids: Callable[[], str] | None = None,
        request_ids: Callable[[], str] | None = None,
        store: Any | None = None,
    ) -> None:
        self._sessions = sessions
        self._extractor = extractor
        self._recorder = recorder if recorder is not None else ListUsageRecorder()
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)
        self._interaction_ids = interaction_ids or (lambda: f"interaction_{uuid4().hex}")
        self._request_ids = request_ids or (lambda: f"request_{uuid4().hex}")
        self._store = store
        self._history: dict[str, list[ProteinDesignRequestDraft]] = {}
        self._messages: dict[str, list[str]] = {}
        self._lock = threading.Lock()
        if store is not None:
            self._install(store.load())

    def revise(self, command: DesignDraftCommand) -> DesignDraftReply:
        # ponytail: one lock spans lookup, revision check, extraction, and append;
        # per-request locks if draft throughput matters.
        with self._lock:
            return self._revise(command)

    def _revise(self, command: DesignDraftCommand) -> DesignDraftReply:
        interaction_id = self._interaction_ids()
        if command.session_id not in self._sessions:
            return self._closed(command, "session_not_found", _SESSION_TEXT, interaction_id)
        found, previous = self._lookup(command)
        if _blocked(command.message):
            return self._closed(
                command,
                "forbidden_action",
                _FORBIDDEN_TEXT,
                interaction_id,
                previous,
            )
        if found == "draft_not_found":
            return self._closed(command, "draft_not_found", _DRAFT_TEXT, interaction_id)
        if found == "revision_conflict":
            return self._closed(
                command,
                "revision_conflict",
                _CONFLICT_TEXT,
                interaction_id,
                previous,
            )
        if _isolated_confirmation(command.message):
            return self._closed(
                command,
                "confirmation_not_available_until_phase_6d",
                _CONFIRM_TEXT,
                interaction_id,
                previous,
            )
        if previous is None and not _draft_intent(command.message):
            return self._closed(command, "unsupported_message", _UNSUPPORTED_TEXT, interaction_id)
        if command.clarifications:
            if previous is None or self._message_of(previous) != command.message:
                status = "unsupported_message" if previous is None else "revision_conflict"
                text = _UNSUPPORTED_TEXT if previous is None else _CONFLICT_TEXT
                return self._closed(command, status, text, interaction_id, previous)
        try:
            patch = self._extractor.extract(message=command.message, draft=previous)
        except Exception:  # noqa: BLE001 — replaceable adapters raise arbitrary errors
            return self._failed(command, interaction_id)
        if not isinstance(patch, DraftPatch):
            return self._failed(command, interaction_id)
        if command.clarifications:
            patch = DraftPatch(
                patch.proposals + _clarification_proposals(command.clarifications),
                patch.usage,
            )
        usage = self._usage(command, patch.usage, interaction_id)
        if isinstance(usage, DesignDraftReply):
            return usage
        fields, turn_issues, changed = _apply(previous, patch.proposals)
        structural = _structural_issues(fields)
        if command.clarifications and any(
            issue.code in {"invalid", "unknown", "rejected"} for issue in turn_issues
        ):
            return self._reply(
                command,
                "invalid_patch",
                _INVALID_TEXT,
                previous,
                turn_issues + structural,
                (),
                usage,
            )
        if not changed:
            issues = turn_issues + structural
            if previous is not None and self._text_differs(previous, command.message):
                if any(issue.code in {"invalid", "unknown", "rejected"} for issue in turn_issues):
                    return self._reply(command, "invalid_patch", _INVALID_TEXT, previous, issues, (), usage)
                return self._revise_recorded_text(command, previous, structural, issues, usage)
            request = None
            if any(issue.code in {"invalid", "unknown", "rejected"} for issue in turn_issues):
                status, text = "invalid_patch", _INVALID_TEXT
            elif previous is not None and previous.completeness == "complete" and not structural:
                status, text = "request_complete", _COMPLETE_TEXT
                request = _request_from(previous)
            else:
                status, text = "clarification_needed", _CLARIFY_TEXT
            return self._reply(command, status, text, previous, issues, (), usage, request)
        moment = _aware(self._clock())
        created_at = previous.created_at if previous is not None else moment
        request_id = previous.request_id if previous is not None else self._request_ids()
        revision = 1 if previous is None else previous.revision + 1
        completeness = "complete" if not structural else "incomplete"
        draft = ProteinDesignRequestDraft(
            request_id=request_id,
            session_id=command.session_id,
            revision=revision,
            created_at=created_at,
            updated_at=moment,
            completeness=completeness,
            fields=_ordered(fields),
            issues=structural,
        )
        if self._commit(draft, command.message) is None:
            current = self._history.get(request_id, [])
            latest = current[-1] if current else None
            return self._closed(
                command,
                "revision_conflict",
                _CONFLICT_TEXT,
                interaction_id,
                latest,
            )
        request = _request_from(draft)
        if request is not None:
            status = "request_complete"
            text = _COMPLETE_TEXT
        elif previous is None:
            status = "draft_created"
            text = _CREATED_TEXT
        else:
            status = "draft_updated"
            text = _UPDATED_TEXT
        return self._reply(
            command,
            status,
            text,
            draft,
            turn_issues + structural,
            changed,
            usage,
            request,
        )

    def _lookup(
        self,
        command: DesignDraftCommand,
    ) -> tuple[str | None, ProteinDesignRequestDraft | None]:
        if command.request_id is None:
            return None, None
        history = self._history.get(command.request_id, [])
        if not history or history[-1].session_id != command.session_id:
            return "draft_not_found", None
        latest = history[-1]
        if command.revision is not None and command.revision != latest.revision:
            return "revision_conflict", latest
        return None, latest

    def revisions(self, request_id: str) -> tuple[ProteinDesignRequestDraft, ...]:
        with self._lock:
            return tuple(self._history.get(request_id, ()))

    def catalog(self) -> tuple[tuple[ProteinDesignRequestDraft, str], ...]:
        """Latest revision of each request. Older revisions stay stored and unlisted."""

        with self._lock:
            items: list[tuple[ProteinDesignRequestDraft, str]] = []
            for request_id, history in self._history.items():
                messages = self._messages.get(request_id, [])
                if not history or len(history) != len(messages):
                    continue
                items.append((history[-1], messages[-1]))
            items.sort(key=lambda item: (item[0].updated_at, item[0].request_id), reverse=True)
            return tuple(items)

    def recorded(self, request_id: str) -> tuple[ProteinDesignRequestDraft, str] | None:
        """Latest saved revision and the text that created it. Read-only."""

        with self._lock:
            history = self._history.get(request_id, [])
            messages = self._messages.get(request_id, [])
            if not history or len(history) != len(messages):
                return None
            return history[-1], messages[-1]

    def recorded_at(self, request_id: str, revision: int) -> tuple[ProteinDesignRequestDraft, str] | None:
        """One saved revision and the text that created it. Read-only."""

        with self._lock:
            history = self._history.get(request_id, [])
            messages = self._messages.get(request_id, [])
            if len(history) != len(messages):
                return None
            for draft, message in zip(history, messages, strict=True):
                if draft.revision == revision:
                    return draft, message
            return None

    def current_for_session(self, session_id: str) -> ProteinDesignRequestDraft | None:
        """Latest draft in this session. Read-only. It does not approve."""

        with self._lock:
            found = [
                history[-1]
                for history in self._history.values()
                if history and history[-1].session_id == session_id
            ]
        if not found:
            return None
        return max(found, key=lambda item: (item.updated_at, item.revision, item.request_id))

    def bind_pdl1_selection(
        self,
        *,
        session_id: str,
        request_id: str,
        selection: Pdl1InputSelection,
    ) -> ProteinDesignRequestDraft:
        """Append a revision that names verified digests. The browser supplies none."""

        with self._lock:
            history = self._history.get(request_id, [])
            if not history or history[-1].session_id != session_id:
                raise ApplicationContractError("pdl1_selection_rejected")
            current = history[-1]
            request = _request_from(current)
            if (
                type(selection) is not Pdl1InputSelection
                or request is None
                or request.generator != "bindcraft"
                or request.target_id != "PDL1"
                or current.completeness != "complete"
            ):
                raise ApplicationContractError("pdl1_selection_rejected")
            draft = replace(
                current,
                revision=current.revision + 1,
                updated_at=_aware(self._clock()),
                input_selection=selection,
            )
            saved = self._commit(draft, self._messages[request_id][-1])
            if saved is None:
                raise ApplicationContractError("pdl1_selection_rejected")
            return saved

    def _message_of(self, previous: ProteinDesignRequestDraft) -> str | None:
        history = self._history.get(previous.request_id, [])
        messages = self._messages.get(previous.request_id, [])
        if len(history) != len(messages):
            return None
        for draft, message in zip(history, messages, strict=True):
            if draft.revision == previous.revision:
                return message
        return None

    def _text_differs(self, previous: ProteinDesignRequestDraft, message: str) -> bool:
        stored = self._messages.get(previous.request_id, [])
        return not stored or stored[-1] != message

    def _revise_recorded_text(
        self,
        command: DesignDraftCommand,
        previous: ProteinDesignRequestDraft,
        structural: tuple[ValidationIssue, ...],
        issues: tuple[ValidationIssue, ...],
        usage: ConversationInstrumentation,
    ) -> DesignDraftReply:
        """Keep the same request when only the researcher's text changed."""

        moment = _aware(self._clock())
        draft = replace(
            previous,
            revision=previous.revision + 1,
            updated_at=moment,
            completeness="complete" if not structural else "incomplete",
            issues=structural,
        )
        if self._commit(draft, command.message) is None:
            current = self._history.get(previous.request_id, [])
            latest = current[-1] if current else None
            return self._closed(
                command,
                "revision_conflict",
                _CONFLICT_TEXT,
                self._interaction_ids(),
                latest,
            )
        request = _request_from(draft)
        if request is not None:
            status, text = "request_complete", _COMPLETE_TEXT
        else:
            status, text = "draft_updated", _UPDATED_TEXT
        return self._reply(command, status, text, draft, issues, (), usage, request)

    def _install(self, documents: list[dict[str, Any]]) -> None:
        grouped: dict[str, list[tuple[ProteinDesignRequestDraft, str]]] = {}
        for document in documents:
            draft, message = _stored_draft(document)
            grouped.setdefault(draft.request_id, []).append((draft, message))
        for request_id, items in grouped.items():
            revisions = [item[0].revision for item in items]
            if revisions != list(range(1, len(items) + 1)):
                raise ApplicationContractError("invalid_stored_draft")
            self._history[request_id] = [item[0] for item in items]
            self._messages[request_id] = [item[1] for item in items]

    def _replace_from_store(self, request_id: str) -> None:
        if self._store is None:
            return
        documents = self._store.revisions(request_id)
        drafts: list[ProteinDesignRequestDraft] = []
        messages: list[str] = []
        for document in documents:
            draft, message = _stored_draft(document)
            if draft.request_id != request_id:
                raise ApplicationContractError("invalid_stored_draft")
            drafts.append(draft)
            messages.append(message)
        if [item.revision for item in drafts] != list(range(1, len(drafts) + 1)):
            raise ApplicationContractError("invalid_stored_draft")
        if drafts:
            self._history[request_id] = drafts
            self._messages[request_id] = messages

    def _commit(self, draft: ProteinDesignRequestDraft, message: str) -> ProteinDesignRequestDraft | None:
        """Insert one revision. A lost race reloads the stored row and writes nothing."""

        if self._store is not None:
            try:
                self._store.append({"message": message, "draft": draft.to_dict()})
            except DraftRevisionConflict:
                self._replace_from_store(draft.request_id)
                return None
        self._history.setdefault(draft.request_id, []).append(draft)
        self._messages.setdefault(draft.request_id, []).append(message)
        return draft

    def _usage(
        self,
        command: DesignDraftCommand,
        report: ModelUsageReport | None,
        interaction_id: str,
    ) -> ConversationInstrumentation | DesignDraftReply:
        if report is None:
            return _unused(interaction_id)
        try:
            report.validate()
        except ApplicationContractError:
            return self._failed(command, interaction_id)
        moment = _aware(self._clock())
        event = ModelUsageEvent(
            session_id=command.session_id,
            agent_id=_AGENT_ID,
            stage=_STAGE,
            provider=report.provider,
            model=report.model,
            started_at=moment,
            completed_at=moment,
            run_id=None,
            input_tokens=report.input_tokens,
            output_tokens=report.output_tokens,
            cached_input_tokens=report.cached_input_tokens,
        )
        try:
            self._recorder.record(event)
        except Exception:  # noqa: BLE001 — telemetry must not authorize anything
            recorded = False
            observation = "telemetry_failed"
        else:
            recorded = True
            observation = "recorded"
        return ConversationInstrumentation(
            interaction_id=interaction_id,
            recorded=recorded,
            event=event,
            total_tokens=report.total_tokens,
            observation=observation,
        )

    def _reply(
        self,
        command: DesignDraftCommand,
        status: str,
        text: str,
        draft: ProteinDesignRequestDraft | None,
        issues: tuple[ValidationIssue, ...],
        changed: tuple[str, ...],
        instrumentation: ConversationInstrumentation,
        request: ProteinDesignRequest | None = None,
    ) -> DesignDraftReply:
        return DesignDraftReply(
            status=status,
            text=text,
            session_id=command.session_id,
            draft=draft,
            request=request,
            questions=_questions(issues),
            issues=issues,
            changed_fields=changed,
            instrumentation=instrumentation,
        )

    def _closed(
        self,
        command: DesignDraftCommand,
        status: str,
        text: str,
        interaction_id: str,
        draft: ProteinDesignRequestDraft | None = None,
    ) -> DesignDraftReply:
        return DesignDraftReply(
            status=status,
            text=text,
            session_id=command.session_id,
            draft=draft,
            request=None,
            questions=(),
            issues=(),
            changed_fields=(),
            instrumentation=_unused(interaction_id),
        )

    def _failed(self, command: DesignDraftCommand, interaction_id: str) -> DesignDraftReply:
        return DesignDraftReply(
            status="adapter_failure",
            text=_ADAPTER_TEXT,
            session_id=command.session_id,
            draft=None,
            request=None,
            questions=(),
            issues=(),
            changed_fields=(),
            instrumentation=ConversationInstrumentation(
                interaction_id=interaction_id,
                recorded=False,
                event=None,
                total_tokens=None,
                observation="adapter_failure",
            ),
        )
