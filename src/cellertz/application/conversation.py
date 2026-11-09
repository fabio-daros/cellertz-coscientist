"""Read-only conversational access over application session contracts.

The use case classifies a question in code, reads through a port, and
asks a model adapter to phrase only the facts that port returned.
It cannot start a run, approve a request, or promote memory.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from cellertz.application.contracts import (
    ApplicationContractError,
    ModelUsageEvent,
    WorkspaceSession,
    _aware,
    _count,
    _text,
)

CONVERSATIONAL_READONLY_CAPABILITIES = frozenset(
    {
        "get_session",
        "get_run",
        "inspect_run_state",
        "list_candidates",
        "get_candidate",
        "get_ranking",
        "get_critique",
        "get_evidence",
        "get_artifacts",
        "get_episodic_memory",
        "get_design_request",
        "get_semantic_knowledge",
        "get_fixture_results",
    }
)

BLOCKED_CAPABILITIES = frozenset(
    {
        "protein_start_fixture_run",
        "create_protein_design_request",
        "approve_request",
        "start_run",
        "resume_run",
        "execute_bindcraft",
        "execute_boltz2",
        "write_tool",
        "mutate_candidate",
        "mutate_metric",
        "mutate_ranking",
        "ingest_memory",
        "promote_memory",
        "promote_second_brain",
        "mutate_session",
        "mutate_persistence",
    }
)

REPLY_STATUSES = frozenset(
    {
        "answered",
        "session_not_found",
        "run_not_found",
        "run_not_in_session",
        "unsupported_question",
        "forbidden_action",
        "adapter_failure",
        "provider_budget",
        "insufficient_data",
    }
)

_AGENT_ID = "readonly_conversation"
_STAGE = "conversation"
_FORBIDDEN_TEXT = (
    "This action is blocked. Conversational access is read-only and cannot "
    "create a request, approve, execute, mutate, ingest, or promote."
)
_UNSUPPORTED_TEXT = "This question is not supported by read-only conversational access."
_INSUFFICIENT_TEXT = "The recorded data is insufficient to answer this question."
_ADAPTER_TEXT = "The conversational adapter failed. No scientific action was taken."
_BUDGET_TEXT = (
    "The language provider reached its credit or completion-token budget. "
    "No scientific action was taken."
)
_FACT_SOURCES = frozenset(
    {"session", "episodic", "design_request", "second_brain", "fixture", "workflow_trace"}
)

# ponytail: phrase list, not a parser. "executed" is a past read;
# a bare "execute" is an order.
_FORBIDDEN_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "create_protein_design_request",
        (
            "proteindesignrequest",
            "create a protein design request",
            "create protein design request",
            "crie um protein design request",
            "criar protein design request",
            "gerar protein design request",
        ),
    ),
    ("approve_request", ("approve", "aprovar")),
    (
        "start_or_execute",
        (
            "start the run",
            "start a run",
            "iniciar a execução",
            "iniciar a execucao",
            "resume the run",
            "resume execution",
            "retomar",
            "run bindcraft",
            "executar bindcraft",
            "run boltz",
            "execute boltz",
            "rodar boltz",
            "start the pipeline",
            "rodar o pipeline",
        ),
    ),
    (
        "mutate_ranking",
        (
            "change the ranking",
            "alter the ranking",
            "alterar o ranking",
            "change the score",
            "alterar o score",
            "alterar a métrica",
            "alterar a metrica",
            "mutate the candidate",
            "alterar o candidato",
        ),
    ),
    (
        "promote_or_ingest",
        ("promote", "promover", "ingest", "ingerir", "second brain"),
    ),
    (
        "mutate_persistence",
        (
            "change the session",
            "alterar a sessão",
            "alterar a sessao",
            "write the file",
            "write tool",
            "ferramenta de escrita",
            "alterar o arquivo",
        ),
    ),
)

_INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("get_design_request", ("design request", "clarification", "draft question")),
    ("get_semantic_knowledge", ("validated knowledge", "knowledge package")),
    ("get_fixture_results", ("fixture result", "fixture run", "workflow stage", "stage record")),
    ("get_ranking", ("ranking", "ipsae", "score", "metric", "métrica", "metrica")),
    ("get_critique", ("critic", "critique", "crítica", "critica", "priorit")),
    ("get_evidence", ("evidence", "evidência", "evidencia")),
    ("get_artifacts", ("artifact", "artefato", "provenance", "proveniência", "proveniencia")),
    ("get_candidate", ("candidate", "candidato")),
    ("inspect_run_state", ("stage", "etapa", "pipeline", "status", "estado")),
    ("get_episodic_memory", ("episodic", "episódica", "episodica", "memory", "memória", "memoria")),
    ("get_run", ("run", "execução", "execucao", "executed", "executado", "ran")),
    ("get_session", ("session", "sessão", "sessao")),
)


def require_readonly(capability: str) -> str:
    if capability not in CONVERSATIONAL_READONLY_CAPABILITIES:
        raise ApplicationContractError("capability_not_allowlisted")
    if capability in BLOCKED_CAPABILITIES:
        raise ApplicationContractError("capability_not_allowlisted")
    return capability


def _orders_execution(text: str) -> bool:
    """Past 'executed' is a read. A remaining 'execute' is an order."""
    if "execute" in text.replace("executed", " ") or "executar" in text:
        return True
    for code, phrases in _FORBIDDEN_RULES:
        if code == "start_or_execute" and any(phrase in text for phrase in phrases):
            return True
    return False


def forbidden_action(question: str) -> str | None:
    folded = question.casefold()
    if _orders_execution(folded):
        return "start_or_execute"
    for code, phrases in _FORBIDDEN_RULES:
        if code == "start_or_execute":
            continue
        if any(phrase in folded for phrase in phrases):
            return code
    return None


def requested_capabilities(question: str, candidate_id: str | None = None) -> tuple[str, ...]:
    if forbidden_action(question):
        return ()
    folded = question.casefold()
    found: list[str] = []
    for capability, phrases in _INTENT_RULES:
        if not any(phrase in folded for phrase in phrases):
            continue
        if capability == "get_candidate":
            found.append("list_candidates")
            if candidate_id:
                found.append("get_candidate")
            continue
        found.append(capability)
    return tuple(dict.fromkeys(found))


def show_recorded(value: object) -> str:
    if value is None:
        return "not_recorded"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return str(value)


@dataclass(frozen=True)
class ReadOnlyFact:
    capability: str
    entity_kind: str
    entity_id: str
    source: str
    attributes: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        require_readonly(self.capability)
        _text(self.entity_kind, "invalid_entity_kind")
        _text(self.entity_id, "invalid_entity_id")
        if self.source not in _FACT_SOURCES:
            raise ApplicationContractError("invalid_fact_source")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "entity_kind": self.entity_kind,
            "entity_id": self.entity_id,
            "source": self.source,
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True)
class QueryPage:
    found: bool
    facts: tuple[ReadOnlyFact, ...]
    code: str | None = None


class ReadOnlyQuery(Protocol):
    def query(
        self,
        capability: str,
        *,
        session_id: str,
        run_id: str | None = None,
        candidate_id: str | None = None,
    ) -> QueryPage: ...


@dataclass(frozen=True)
class ModelUsageReport:
    """Token fields stay None unless the provider reported them."""

    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None

    def validate(self) -> None:
        _text(self.provider, "invalid_provider")
        _text(self.model, "invalid_model")
        for value in (
            self.input_tokens,
            self.output_tokens,
            self.total_tokens,
            self.cached_input_tokens,
        ):
            _count(value)


@dataclass(frozen=True)
class ResponseOrigin:
    """Provider and model identity. Token counts stay on the usage report."""

    provider: str
    model: str

    def __post_init__(self) -> None:
        _text(self.provider, "invalid_provider")
        _text(self.model, "invalid_model")

    def to_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "model": self.model}


@dataclass(frozen=True)
class ModelCompletion:
    text: str
    usage: ModelUsageReport | None = None
    origin: ResponseOrigin | None = None


class ConversationModel(Protocol):
    def complete(
        self,
        *,
        facts: tuple[ReadOnlyFact, ...],
        question: str,
    ) -> ModelCompletion: ...


class UsageRecorder(Protocol):
    def record(self, event: ModelUsageEvent) -> None: ...


@dataclass(frozen=True)
class ConversationRequest:
    session_id: str
    question: str
    run_id: str | None = None
    candidate_id: str | None = None

    def __post_init__(self) -> None:
        _text(self.session_id, "invalid_session_id")
        _text(self.question, "empty_message")
        if self.run_id is not None:
            _text(self.run_id, "invalid_run_id")
        if self.candidate_id is not None:
            _text(self.candidate_id, "invalid_candidate_id")


@dataclass(frozen=True)
class ConversationInstrumentation:
    interaction_id: str
    recorded: bool
    event: ModelUsageEvent | None
    total_tokens: int | None
    observation: str
    origin: ResponseOrigin | None = None

    def __post_init__(self) -> None:
        _text(self.interaction_id, "invalid_interaction_id")
        if self.observation not in {
            "recorded",
            "no_model_usage",
            "adapter_failure",
            "telemetry_failed",
        }:
            raise ApplicationContractError("invalid_usage_observation")
        if self.recorded and self.event is None:
            raise ApplicationContractError("recorded_usage_missing_event")
        if self.total_tokens is not None and self.total_tokens < 0:
            raise ApplicationContractError("negative_quantity")

    def to_dict(self) -> dict[str, Any]:
        return {
            "interaction_id": self.interaction_id,
            "recorded": self.recorded,
            "total_tokens": self.total_tokens,
            "observation": self.observation,
            "event": None if self.event is None else self.event.to_dict(),
            "origin": None if self.origin is None else self.origin.to_dict(),
        }


@dataclass(frozen=True)
class ConversationReply:
    status: str
    text: str
    session_id: str
    run_id: str | None
    evidence: tuple[ReadOnlyFact, ...]
    instrumentation: ConversationInstrumentation
    read_only: bool = True

    def __post_init__(self) -> None:
        if self.status not in REPLY_STATUSES:
            raise ApplicationContractError("invalid_reply_status")
        _text(self.text, "empty_message")
        if self.session_id:
            _text(self.session_id, "invalid_session_id")
        if not self.read_only:
            raise ApplicationContractError("conversation_is_read_only")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "text": self.text,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "read_only": True,
            "evidence": [fact.to_dict() for fact in self.evidence],
            "instrumentation": self.instrumentation.to_dict(),
        }


class ListUsageRecorder:
    def __init__(self) -> None:
        self.events: list[ModelUsageEvent] = []

    def record(self, event: ModelUsageEvent) -> None:
        self.events.append(event)


class RecordedFactsModel:
    """Phrases only the facts it was given. It reports no provider usage."""

    def complete(
        self,
        *,
        facts: tuple[ReadOnlyFact, ...],
        question: str,
    ) -> ModelCompletion:
        del question
        lines = []
        for fact in facts:
            rendered = ", ".join(f"{key}={value}" for key, value in fact.attributes)
            lines.append(f"{fact.entity_kind} {fact.entity_id}: {rendered}")
        text = "Read-only. " + (" ".join(lines) if lines else "No recorded facts were supplied.")
        return ModelCompletion(text=text, usage=None)


class SessionSnapshotQuery:
    """Reads a WorkspaceSession already held in memory. No files, no MCP."""

    def __init__(self, sessions: Mapping[str, WorkspaceSession]) -> None:
        self._sessions = dict(sessions)

    def query(
        self,
        capability: str,
        *,
        session_id: str,
        run_id: str | None = None,
        candidate_id: str | None = None,
    ) -> QueryPage:
        require_readonly(capability)
        session = self._sessions.get(session_id)
        if session is None:
            return QueryPage(found=False, facts=())
        if capability == "get_session":
            return QueryPage(True, (_session_fact(session),))
        if capability == "get_run":
            return _run_page(session, run_id)
        if capability == "inspect_run_state":
            return QueryPage(True, _stage_facts(session))
        if capability == "list_candidates":
            return QueryPage(True, _candidate_facts(session, None, "list_candidates"))
        if capability == "get_candidate":
            return QueryPage(True, _candidate_facts(session, candidate_id, "get_candidate"))
        if capability == "get_ranking":
            return QueryPage(True, _ranking_facts(session, candidate_id))
        if capability == "get_evidence":
            return QueryPage(True, _evidence_facts(session, candidate_id))
        if capability == "get_artifacts":
            return QueryPage(True, _artifact_facts(session, candidate_id))
        if capability == "get_episodic_memory":
            return _episodic_snapshot(session, run_id)
        return QueryPage(found=True, facts=())


def _session_fact(session: WorkspaceSession) -> ReadOnlyFact:
    return ReadOnlyFact(
        capability="get_session",
        entity_kind="session",
        entity_id=session.session_id,
        source="session",
        attributes=(
            ("target_id", session.project.target_id),
            ("design_goal", session.project.design_goal),
            ("execution_authorized", "false"),
        ),
    )


def _run_page(session: WorkspaceSession, run_id: str | None) -> QueryPage:
    if run_id is None:
        return QueryPage(found=False, facts=())
    match = next((run for run in session.runs if run.run_id == run_id), None)
    if match is None:
        return QueryPage(found=False, facts=())
    return QueryPage(
        True,
        (
            ReadOnlyFact(
                capability="get_run",
                entity_kind="run",
                entity_id=match.run_id,
                source="session",
                attributes=(
                    ("target_id", match.target_id),
                    ("memory_house", match.memory_house),
                    ("epistemic_status", match.epistemic_status),
                    ("display_status", match.display_status),
                ),
            ),
        ),
    )


def _stage_facts(session: WorkspaceSession) -> tuple[ReadOnlyFact, ...]:
    return tuple(
        ReadOnlyFact(
            capability="inspect_run_state",
            entity_kind="stage",
            entity_id=stage.stage_id,
            source="session",
            attributes=(
                ("label", stage.label),
                ("display_status", stage.display_status),
                ("detail", stage.detail),
            ),
        )
        for stage in session.pipeline
    )


def _candidate_facts(
    session: WorkspaceSession,
    candidate_id: str | None,
    capability: str,
) -> tuple[ReadOnlyFact, ...]:
    rows = session.candidates
    if candidate_id is not None:
        rows = tuple(item for item in rows if item.candidate_id == candidate_id)
    return tuple(
        ReadOnlyFact(
            capability=capability,
            entity_kind="candidate",
            entity_id=item.candidate_id,
            source="session",
            attributes=(
                ("candidate_kind", item.candidate_kind),
                ("epistemic_status", item.epistemic_status),
                ("provenance", item.provenance),
            ),
        )
        for item in rows
    )


def _ranking_facts(
    session: WorkspaceSession,
    candidate_id: str | None,
) -> tuple[ReadOnlyFact, ...]:
    rows = session.candidates
    if candidate_id is not None:
        rows = tuple(item for item in rows if item.candidate_id == candidate_id)
    return tuple(
        ReadOnlyFact(
            capability="get_ranking",
            entity_kind="candidate",
            entity_id=item.candidate_id,
            source="session",
            attributes=(
                ("ranking_signal", item.ranking_signal),
                ("ranking_score", show_recorded(item.ranking_score)),
                ("rank", show_recorded(item.rank)),
                ("is_probability", "false"),
                ("recommended_as_hard_filter", "false"),
                ("score_label", item.score_label),
            ),
        )
        for item in rows
    )


def _evidence_facts(
    session: WorkspaceSession,
    candidate_id: str | None,
) -> tuple[ReadOnlyFact, ...]:
    rows = session.evidence
    if candidate_id is not None:
        rows = tuple(item for item in rows if item.candidate_id == candidate_id)
    return tuple(
        ReadOnlyFact(
            capability="get_evidence",
            entity_kind="evidence",
            entity_id=item.evidence_id,
            source="session",
            attributes=(
                ("candidate_id", item.candidate_id),
                ("kind", item.kind),
                ("summary", item.summary),
                ("epistemic_status", item.epistemic_status),
            ),
        )
        for item in rows
    )


def _artifact_facts(
    session: WorkspaceSession,
    candidate_id: str | None,
) -> tuple[ReadOnlyFact, ...]:
    rows = session.artifacts
    if candidate_id is not None:
        rows = tuple(item for item in rows if item.candidate_id == candidate_id)
    return tuple(
        ReadOnlyFact(
            capability="get_artifacts",
            entity_kind="artifact",
            entity_id=item.artifact_id,
            source="session",
            attributes=(
                ("candidate_id", item.candidate_id),
                ("kind", item.kind),
                ("uri", item.uri),
                ("media_type", item.media_type),
            ),
        )
        for item in rows
    )


def _episodic_snapshot(session: WorkspaceSession, run_id: str | None) -> QueryPage:
    runs = session.runs
    if run_id is not None:
        runs = tuple(run for run in runs if run.run_id == run_id)
    return QueryPage(
        True,
        tuple(
            ReadOnlyFact(
                capability="get_episodic_memory",
                entity_kind="run",
                entity_id=run.run_id,
                source="session",
                attributes=(
                    ("memory_house", run.memory_house),
                    ("epistemic_status", run.epistemic_status),
                    ("scientific_run_memory", "not_attached"),
                ),
            )
            for run in runs
        ),
    )


class ReadOnlyConversation:
    def __init__(
        self,
        sources: ReadOnlyQuery,
        model: ConversationModel | None = None,
        recorder: UsageRecorder | None = None,
        clock: Callable[[], datetime] | None = None,
        interaction_ids: Callable[[], str] | None = None,
    ) -> None:
        self._sources = sources
        self._model = model or RecordedFactsModel()
        self._recorder = recorder or ListUsageRecorder()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ids = interaction_ids or (lambda: uuid4().hex)

    def ask(self, request: ConversationRequest) -> ConversationReply:
        interaction_id = self._ids()
        if forbidden_action(request.question):
            return self._closed(
                request,
                "forbidden_action",
                _FORBIDDEN_TEXT,
                interaction_id,
            )
        capabilities = requested_capabilities(request.question, request.candidate_id)
        if not capabilities:
            return self._closed(
                request,
                "unsupported_question",
                _UNSUPPORTED_TEXT,
                interaction_id,
            )
        for capability in capabilities:
            require_readonly(capability)
        session = self._read("get_session", request)
        if not session.found:
            return self._closed(
                request,
                "session_not_found",
                f"No session is recorded for {request.session_id}.",
                interaction_id,
            )
        facts = list(session.facts)
        if request.run_id is not None:
            run = self._read("get_run", request)
            if not run.found:
                if run.code == "run_not_in_session":
                    return self._closed(
                        request,
                        "run_not_in_session",
                        "This run is not linked to this session.",
                        interaction_id,
                    )
                return self._closed(
                    request,
                    "run_not_found",
                    f"No scientific run is recorded for {request.run_id}.",
                    interaction_id,
                )
            facts.extend(run.facts)
        data_capabilities = tuple(
            capability for capability in capabilities if capability not in {"get_session", "get_run"}
        )
        data_facts: list[ReadOnlyFact] = []
        for capability in data_capabilities:
            data_facts.extend(self._read(capability, request).facts)
        if data_capabilities and not data_facts:
            return self._closed(
                request,
                "insufficient_data",
                _INSUFFICIENT_TEXT,
                interaction_id,
            )
        facts.extend(data_facts)
        return self._complete(request, tuple(facts), interaction_id)

    def _read(self, capability: str, request: ConversationRequest) -> QueryPage:
        require_readonly(capability)
        return self._sources.query(
            capability,
            session_id=request.session_id,
            run_id=request.run_id,
            candidate_id=request.candidate_id,
        )

    def _complete(
        self,
        request: ConversationRequest,
        facts: tuple[ReadOnlyFact, ...],
        interaction_id: str,
    ) -> ConversationReply:
        try:
            completion = self._model.complete(facts=facts, question=request.question)
        except Exception as exc:  # noqa: BLE001 — replaceable adapters raise arbitrary errors
            if type(exc) is RuntimeError and str(exc) == "provider_budget":
                return self._failed(request, interaction_id, budget=True)
            return self._failed(request, interaction_id)
        if not isinstance(completion.text, str) or not completion.text.strip():
            return self._failed(request, interaction_id)
        event: ModelUsageEvent | None = None
        total: int | None = None
        recorded = False
        observation = "no_model_usage"
        origin = completion.origin
        if completion.usage is not None:
            try:
                completion.usage.validate()
                if origin is None:
                    origin = ResponseOrigin(
                        provider=completion.usage.provider,
                        model=completion.usage.model,
                    )
                event = self._event(request, completion.usage)
            except ApplicationContractError:
                return self._failed(request, interaction_id)
            total = completion.usage.total_tokens
            try:
                self._recorder.record(event)
            except Exception:  # noqa: BLE001 — telemetry must not replace the answer
                observation = "telemetry_failed"
            else:
                recorded = True
                observation = "recorded"
        return ConversationReply(
            status="answered",
            text=completion.text,
            session_id=request.session_id,
            run_id=request.run_id,
            evidence=facts,
            instrumentation=ConversationInstrumentation(
                interaction_id=interaction_id,
                recorded=recorded,
                event=event,
                total_tokens=total,
                observation=observation,
                origin=origin,
            ),
        )

    def _event(self, request: ConversationRequest, usage: ModelUsageReport) -> ModelUsageEvent:
        moment = _aware(self._clock())
        return ModelUsageEvent(
            session_id=request.session_id,
            agent_id=_AGENT_ID,
            stage=_STAGE,
            provider=usage.provider,
            model=usage.model,
            started_at=moment,
            completed_at=moment,
            run_id=request.run_id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_input_tokens=usage.cached_input_tokens,
        )

    def _closed(
        self,
        request: ConversationRequest,
        status: str,
        text: str,
        interaction_id: str,
    ) -> ConversationReply:
        return ConversationReply(
            status=status,
            text=text,
            session_id=request.session_id,
            run_id=request.run_id,
            evidence=(),
            instrumentation=_unused(interaction_id),
        )

    def _failed(
        self,
        request: ConversationRequest,
        interaction_id: str,
        *,
        budget: bool = False,
    ) -> ConversationReply:
        return ConversationReply(
            status="provider_budget" if budget else "adapter_failure",
            text=_BUDGET_TEXT if budget else _ADAPTER_TEXT,
            session_id=request.session_id,
            run_id=request.run_id,
            evidence=(),
            instrumentation=ConversationInstrumentation(
                interaction_id=interaction_id,
                recorded=False,
                event=None,
                total_tokens=None,
                observation="adapter_failure",
            ),
        )


def _unused(interaction_id: str) -> ConversationInstrumentation:
    return ConversationInstrumentation(
        interaction_id=interaction_id,
        recorded=False,
        event=None,
        total_tokens=None,
        observation="no_model_usage",
    )
