"""Immutable display contracts for the Protein Design workspace.

A draft is not an approved request. A budget is not an execution
authorization. Unknown cost stays unknown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class ApplicationContractError(ValueError):
    """Stable application-contract failure. The message is the code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApplicationContractError(code)
    return value


def _choice(value: str, allowed: frozenset[str], code: str) -> str:
    if value not in allowed:
        raise ApplicationContractError(code)
    return value


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ApplicationContractError("naive_timestamp")
    return value


def _count(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ApplicationContractError("negative_quantity")
    return value


def _money(value: float | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ApplicationContractError("negative_quantity")
    return float(value)


def _stamp(value: datetime) -> str:
    return _aware(value).isoformat()


def _artifact_uri(value: str) -> str:
    """Phase 6A accepts artifact:// metadata only, never a file location."""
    _text(value, "invalid_artifact_uri")
    prefix = "artifact://"
    if not value.startswith(prefix) or "\\" in value or "file://" in value:
        raise ApplicationContractError("artifact_uri_is_metadata_only")
    parts = value[len(prefix):].split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ApplicationContractError("artifact_uri_is_metadata_only")
    return value


@dataclass(frozen=True)
class ConversationMessage:
    message_id: str
    role: str
    created_at: datetime
    content: str

    def __post_init__(self) -> None:
        _text(self.message_id, "invalid_message_id")
        _choice(self.role, frozenset({"scientist", "system", "assistant"}), "invalid_message_role")
        _aware(self.created_at)
        _text(self.content, "empty_message")

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "role": self.role,
            "created_at": _stamp(self.created_at),
            "content": self.content,
        }


@dataclass(frozen=True)
class ProteinDesignDraft:
    """A scientist's unfinished request. Never executable."""

    draft_id: str
    target_id: str
    design_goal: str
    status: str
    executable: bool = False

    def __post_init__(self) -> None:
        _text(self.draft_id, "invalid_draft_id")
        _text(self.target_id, "invalid_target_id")
        _text(self.design_goal, "invalid_design_goal")
        _choice(self.status, frozenset({"incomplete", "ready_for_review"}), "invalid_draft_status")
        if self.executable:
            raise ApplicationContractError("draft_is_not_executable")

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "target_id": self.target_id,
            "design_goal": self.design_goal,
            "status": self.status,
            "executable": False,
        }


@dataclass(frozen=True)
class ProjectContext:
    project_id: str
    target_id: str
    design_goal: str
    prototype_label: str

    def __post_init__(self) -> None:
        _text(self.project_id, "invalid_project_id")
        _text(self.target_id, "invalid_target_id")
        _text(self.design_goal, "invalid_design_goal")
        _text(self.prototype_label, "invalid_prototype_label")

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "target_id": self.target_id,
            "design_goal": self.design_goal,
            "prototype_label": self.prototype_label,
        }


@dataclass(frozen=True)
class PipelineStageSnapshot:
    stage_id: str
    label: str
    display_status: str
    detail: str

    def __post_init__(self) -> None:
        _text(self.stage_id, "invalid_stage_id")
        _text(self.label, "invalid_stage_label")
        _choice(
            self.display_status,
            frozenset({"shown", "future", "not_executed"}),
            "invalid_stage_status",
        )
        _text(self.detail, "invalid_stage_detail")

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "label": self.label,
            "display_status": self.display_status,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class AgentSnapshot:
    agent_id: str
    role: str
    display_status: str

    def __post_init__(self) -> None:
        _text(self.agent_id, "invalid_agent_id")
        _text(self.role, "invalid_agent_role")
        _choice(self.display_status, frozenset({"idle", "shown"}), "invalid_agent_status")

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "display_status": self.display_status,
        }


@dataclass(frozen=True)
class CandidateSummary:
    """Display row for one candidate. ranking_score is raw ipSAE or unknown."""

    candidate_id: str
    candidate_kind: str
    epistemic_status: str
    rank: int | None
    ranking_signal: str
    ranking_score: float | None
    score_label: str
    provenance: str
    is_probability: bool = False
    recommended_as_hard_filter: bool = False

    def __post_init__(self) -> None:
        _text(self.candidate_id, "invalid_candidate_id")
        _choice(
            self.candidate_kind,
            frozenset({"observed_identity", "placeholder"}),
            "invalid_candidate_kind",
        )
        _choice(
            self.epistemic_status,
            frozenset({"observed", "unknown"}),
            "invalid_candidate_epistemic_status",
        )
        if self.candidate_kind == "placeholder" and self.epistemic_status != "unknown":
            raise ApplicationContractError("placeholder_is_not_observed")
        if self.candidate_kind == "observed_identity" and self.epistemic_status != "observed":
            raise ApplicationContractError("observed_identity_must_be_observed")
        if self.rank is not None and (isinstance(self.rank, bool) or self.rank < 1):
            raise ApplicationContractError("invalid_rank")
        if self.ranking_signal != "raw_ipsae":
            raise ApplicationContractError("invalid_ranking_signal")
        if self.is_probability or self.recommended_as_hard_filter:
            raise ApplicationContractError("ipsae_is_not_a_probability_or_filter")
        _money(self.ranking_score)
        _text(self.score_label, "invalid_score_label")
        if "raw ipSAE" not in self.score_label:
            raise ApplicationContractError("score_must_name_raw_ipsae")
        _text(self.provenance, "missing_provenance")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_kind": self.candidate_kind,
            "epistemic_status": self.epistemic_status,
            "rank": self.rank,
            "ranking_signal": self.ranking_signal,
            "ranking_score": self.ranking_score,
            "score_label": self.score_label,
            "is_probability": False,
            "recommended_as_hard_filter": False,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class EvidenceSummary:
    evidence_id: str
    candidate_id: str
    kind: str
    summary: str
    epistemic_status: str

    def __post_init__(self) -> None:
        _text(self.evidence_id, "invalid_evidence_id")
        _text(self.candidate_id, "invalid_candidate_id")
        _choice(
            self.kind,
            frozenset({"execution_record", "provenance", "qualitative_note"}),
            "invalid_evidence_kind",
        )
        _text(self.summary, "empty_evidence")
        _choice(
            self.epistemic_status,
            frozenset({"observed", "validated", "unknown"}),
            "invalid_epistemic_status",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "candidate_id": self.candidate_id,
            "kind": self.kind,
            "summary": self.summary,
            "epistemic_status": self.epistemic_status,
        }


@dataclass(frozen=True)
class ArtifactSummary:
    artifact_id: str
    candidate_id: str
    kind: str
    uri: str
    media_type: str

    def __post_init__(self) -> None:
        _text(self.artifact_id, "invalid_artifact_id")
        _text(self.candidate_id, "invalid_candidate_id")
        _text(self.kind, "invalid_artifact_kind")
        _artifact_uri(self.uri)
        _text(self.media_type, "invalid_media_type")

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "candidate_id": self.candidate_id,
            "kind": self.kind,
            "uri": self.uri,
            "media_type": self.media_type,
        }


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    target_id: str
    memory_house: str
    epistemic_status: str
    display_status: str

    def __post_init__(self) -> None:
        _text(self.run_id, "invalid_run_id")
        _text(self.target_id, "invalid_target_id")
        if self.memory_house != "episodic":
            raise ApplicationContractError("run_display_is_episodic")
        _choice(
            self.epistemic_status,
            frozenset({"observed", "unknown"}),
            "invalid_epistemic_status",
        )
        _text(self.display_status, "invalid_run_status")

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "target_id": self.target_id,
            "memory_house": self.memory_house,
            "epistemic_status": self.epistemic_status,
            "display_status": self.display_status,
        }


@dataclass(frozen=True)
class ModelUsageEvent:
    session_id: str
    agent_id: str
    stage: str
    provider: str
    model: str
    started_at: datetime
    completed_at: datetime
    run_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    tool_call_count: int | None = None
    estimated_cost: float | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        _text(self.session_id, "invalid_session_id")
        _text(self.agent_id, "invalid_agent_id")
        _text(self.stage, "invalid_stage")
        _text(self.provider, "invalid_provider")
        _text(self.model, "invalid_model")
        _aware(self.started_at)
        _aware(self.completed_at)
        if self.completed_at < self.started_at:
            raise ApplicationContractError("completed_before_started")
        if self.run_id is not None:
            _text(self.run_id, "invalid_run_id")
        for value in (
            self.input_tokens,
            self.output_tokens,
            self.cached_input_tokens,
            self.tool_call_count,
        ):
            _count(value)
        _money(self.estimated_cost)
        if self.currency is not None:
            _text(self.currency, "invalid_currency")
        if (self.estimated_cost is None) != (self.currency is None):
            raise ApplicationContractError("cost_and_currency_must_agree")

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "stage": self.stage,
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "tool_call_count": self.tool_call_count,
            "estimated_cost": self.estimated_cost,
            "currency": self.currency,
            "started_at": _stamp(self.started_at),
            "completed_at": _stamp(self.completed_at),
            "cost_kind": "model",
        }


@dataclass(frozen=True)
class ExecutionBudget:
    """Ceilings for a future run. This object never authorizes execution."""

    token_ceiling: int | None
    model_cost_ceiling: float | None
    scientific_tool_cost_ceiling: float | None
    warning_threshold: float | None
    action_on_limit: str
    human_override_required: bool
    currency: str | None = None
    authorizes_execution: bool = False

    def __post_init__(self) -> None:
        _count(self.token_ceiling)
        _money(self.model_cost_ceiling)
        _money(self.scientific_tool_cost_ceiling)
        if self.warning_threshold is not None and not 0 <= self.warning_threshold <= 1:
            raise ApplicationContractError("invalid_warning_threshold")
        _choice(self.action_on_limit, frozenset({"warn", "stop"}), "invalid_budget_action")
        if self.authorizes_execution:
            raise ApplicationContractError("budget_does_not_authorize_execution")
        has_money_ceiling = (
            self.model_cost_ceiling is not None or self.scientific_tool_cost_ceiling is not None
        )
        if has_money_ceiling and self.currency is None:
            raise ApplicationContractError("cost_and_currency_must_agree")
        if self.currency is not None:
            _text(self.currency, "invalid_currency")

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_ceiling": self.token_ceiling,
            "model_cost_ceiling": self.model_cost_ceiling,
            "scientific_tool_cost_ceiling": self.scientific_tool_cost_ceiling,
            "warning_threshold": self.warning_threshold,
            "action_on_limit": self.action_on_limit,
            "human_override_required": self.human_override_required,
            "currency": self.currency,
            "authorizes_execution": False,
        }


@dataclass(frozen=True)
class UsageBudgetSnapshot:
    session_id: str
    estimated_model_cost: float | None
    scientific_infrastructure_cost: float | None
    currency: str | None

    def __post_init__(self) -> None:
        _text(self.session_id, "invalid_session_id")
        _money(self.estimated_model_cost)
        _money(self.scientific_infrastructure_cost)
        if self.currency is not None:
            _text(self.currency, "invalid_currency")
        costs = (self.estimated_model_cost, self.scientific_infrastructure_cost)
        if any(cost is not None for cost in costs) and self.currency is None:
            raise ApplicationContractError("cost_and_currency_must_agree")

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "estimated_model_cost": self.estimated_model_cost,
            "scientific_infrastructure_cost": self.scientific_infrastructure_cost,
            "currency": self.currency,
            "model_cost_known": self.estimated_model_cost is not None,
            "infrastructure_cost_known": self.scientific_infrastructure_cost is not None,
        }


@dataclass(frozen=True)
class WorkspaceSession:
    session_id: str
    project: ProjectContext
    messages: tuple[ConversationMessage, ...]
    draft: ProteinDesignDraft
    pipeline: tuple[PipelineStageSnapshot, ...]
    agents: tuple[AgentSnapshot, ...]
    candidates: tuple[CandidateSummary, ...]
    evidence: tuple[EvidenceSummary, ...]
    artifacts: tuple[ArtifactSummary, ...]
    runs: tuple[RunSummary, ...]
    usage: tuple[ModelUsageEvent, ...]
    budget: ExecutionBudget
    usage_snapshot: UsageBudgetSnapshot
    execution_authorized: bool = False

    def __post_init__(self) -> None:
        _text(self.session_id, "invalid_session_id")
        if self.execution_authorized or self.draft.executable or self.budget.authorizes_execution:
            raise ApplicationContractError("session_cannot_authorize_execution")
        candidates = self._candidates_by_id()
        for item in self.evidence:
            candidate = candidates.get(item.candidate_id)
            if candidate is None:
                raise ApplicationContractError("unknown_evidence_candidate")
            if candidate.candidate_kind == "placeholder" and item.epistemic_status == "observed":
                raise ApplicationContractError("placeholder_has_observed_evidence")
        for item in self.artifacts:
            candidate = candidates.get(item.candidate_id)
            if candidate is None:
                raise ApplicationContractError("unknown_artifact_candidate")
            if candidate.candidate_kind == "placeholder":
                raise ApplicationContractError("placeholder_has_artifact")

    def _candidates_by_id(self) -> dict[str, CandidateSummary]:
        found: dict[str, CandidateSummary] = {}
        for candidate in self.candidates:
            if candidate.candidate_id in found:
                raise ApplicationContractError("duplicate_candidate_id")
            found[candidate.candidate_id] = candidate
        return found

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "project": self.project.to_dict(),
            "messages": [item.to_dict() for item in self.messages],
            "draft": self.draft.to_dict(),
            "pipeline": [item.to_dict() for item in self.pipeline],
            "agents": [item.to_dict() for item in self.agents],
            "candidates": [item.to_dict() for item in self.candidates],
            "evidence": [item.to_dict() for item in self.evidence],
            "artifacts": [item.to_dict() for item in self.artifacts],
            "runs": [item.to_dict() for item in self.runs],
            "usage": [item.to_dict() for item in self.usage],
            "budget": self.budget.to_dict(),
            "usage_snapshot": self.usage_snapshot.to_dict(),
            "execution_authorized": False,
            "prototype_only": True,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
