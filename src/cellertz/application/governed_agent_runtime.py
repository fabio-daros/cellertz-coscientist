"""Fixed PDL1 agent workflow for one scientific-run attempt.

Stage order and specialist versions are read from the supervisor module
when a stage runs. This module does not construct that supervisor, write
a file, or call a scientific tool. The five planners are deterministic
fixture specialists: they do not emit a sequence, score, or rank.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.scientific_authorization import binding_hash_for
from cellertz.application.scientific_run import (
    _EXECUTION_SCHEMA,
    _PROFILE,
    COMPLETED_TEXT,
    QUEUED_TEXT,
    RETRY_QUEUED_TEXT,
    RUNNING_TEXT,
    ExecutorArtifact,
    ScientificExecutionResult,
    ScientificRun,
    ScientificRunEvent,
    _canonical,
    _digest,
    _safe_text,
    _stamp_ok,
)
from cellertz.domains.protein_design.agents import supervisor as supervisor_contract

CONTRACT_ORDER = ("generation", "scoring", "ranking", "critique", "prioritization")
CONTRACT_VERSIONS = {
    "generation": "protein_generation_agent_v1",
    "scoring": "protein_scoring_tool_v2_fixture_metrics_v1",
    "ranking": "protein_ranking_agent_v1",
    "critique": "protein_critic_agent_v1",
    "prioritization": "protein_prioritization_agent_v1",
}
WORKFLOW_SCHEMA = "cellertz.governed_agent_workflow.v1"
WORKFLOW_PROFILE = "pdl1-governed-agent-workflow"
WORKFLOW_VERSION = "v1"
SUMMARY = (
    "Deterministic agent workflow fixture. "
    "Operational orchestration evidence. "
    "Not scientific validation. "
    "No scientific candidate was generated. "
    "No scientific action was taken."
)
_TRACE_KEYS = frozenset(
    {
        "accepted",
        "attempt",
        "authorization_id",
        "binding_hash",
        "profile_id",
        "ranked",
        "run_id",
        "schema",
        "scientific_candidate_generated",
        "scientific_validation",
        "session_id",
        "stages",
        "target_scope",
        "workflow_profile",
        "workflow_version",
    }
)
_STAGE_KEYS = frozenset(
    {
        "attempt",
        "implementation_id",
        "implementation_version",
        "input_digest",
        "observed_at",
        "output_digest",
        "role",
        "run_id",
        "sequence",
        "stage_id",
        "status",
        "summary",
        "upstream_digest",
    }
)


@dataclass(frozen=True)
class StageHandoff:
    """Verified input for one specialist. The digest is computed upstream."""

    attempt: int
    binding_hash: str
    run_id: str
    sequence: int
    stage_id: str
    upstream_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.attempt) is not int
            or isinstance(self.attempt, bool)
            or self.attempt < 1
            or type(self.sequence) is not int
            or isinstance(self.sequence, bool)
            or self.sequence < 1
            or type(self.binding_hash) is not str
            or type(self.upstream_digest) is not str
            or len(self.binding_hash) != 64
            or len(self.upstream_digest) != 64
            or type(self.run_id) is not str
            or type(self.stage_id) is not str
            or self.stage_id not in CONTRACT_ORDER
        ):
            raise ApplicationContractError("invalid_stage_handoff")


@dataclass(frozen=True)
class StagePlan:
    """Operational fixture plan. Not a scientific result."""

    status: str
    summary: str

    def __post_init__(self) -> None:
        if self.status != "completed" or self.summary != SUMMARY or not _safe_text(self.summary):
            raise ApplicationContractError("invalid_stage_plan")


class _FixtureSpecialist:
    stage_id = ""

    def plan(self, handoff: StageHandoff) -> StagePlan:
        if type(handoff) is not StageHandoff or handoff.stage_id != self.stage_id:
            raise ApplicationContractError("invalid_stage_handoff")
        return StagePlan(status="completed", summary=SUMMARY)


class GenerationPlanningSpecialist(_FixtureSpecialist):
    """Request grounding and design-generation planning. No sequence is produced."""

    stage_id = "generation"


class ScoringPlanningSpecialist(_FixtureSpecialist):
    """Hotspot constraints and screening plan. No biological score is produced."""

    stage_id = "scoring"


class RankingPlanningSpecialist(_FixtureSpecialist):
    """Ranking assessment plan. No biological candidate is ranked."""

    stage_id = "ranking"


class CritiquePlanningSpecialist(_FixtureSpecialist):
    """Scientific-consistency review plan. It does not validate a result."""

    stage_id = "critique"


class PrioritizationPlanningSpecialist(_FixtureSpecialist):
    """Prioritization and explanation plan. It does not accept a candidate."""

    stage_id = "prioritization"


def default_specialists() -> dict[str, _FixtureSpecialist]:
    return {
        "generation": GenerationPlanningSpecialist(),
        "scoring": ScoringPlanningSpecialist(),
        "ranking": RankingPlanningSpecialist(),
        "critique": CritiquePlanningSpecialist(),
        "prioritization": PrioritizationPlanningSpecialist(),
    }


def stage_ids() -> tuple[str, ...] | None:
    """Supervisor order, accepted only when it is this phase's contract."""
    order = supervisor_contract.STAGE_ORDER
    if order != CONTRACT_ORDER:
        return None
    return order


def versions() -> dict[str, str] | None:
    """Supervisor specialist versions, accepted only when they match the contract."""
    live = {
        "generation": supervisor_contract.GENERATION_VERSION,
        "scoring": supervisor_contract.SCORING_VERSION,
        "ranking": supervisor_contract.RANKING_VERSION,
        "critique": supervisor_contract.CRITIQUE_VERSION,
        "prioritization": supervisor_contract.PRIORITIZATION_VERSION,
    }
    if live != CONTRACT_VERSIONS:
        return None
    return live


class GovernedProteinAgentRuntime:
    """Sequential fixture specialists. One pass per claimed attempt."""

    def __init__(self, specialists: Mapping[str, Any] | None = None) -> None:
        self.specialists = default_specialists() if specialists is None else dict(specialists)

    def execute(self, run: ScientificRun) -> ScientificExecutionResult:
        failed = ScientificExecutionResult("failed", ())
        if type(run) is not ScientificRun or not _eligible(run):
            return failed
        order = stage_ids()
        live_versions = versions()
        # New work follows the installed supervisor. A mismatch stores nothing.
        if order is None or live_versions is None:
            return failed
        expected = workflow_observed_at(run)
        if expected is None:
            return failed
        stages: list[dict[str, Any]] = []
        upstream = run.binding_hash
        for sequence, stage_id in enumerate(order, start=1):
            specialist = self.specialists.get(stage_id)
            if specialist is None or getattr(specialist, "stage_id", None) != stage_id:
                return failed
            try:
                handoff = StageHandoff(
                    attempt=run.attempt,
                    binding_hash=run.binding_hash,
                    run_id=run.run_id,
                    sequence=sequence,
                    stage_id=stage_id,
                    upstream_digest=upstream,
                )
                plan = specialist.plan(handoff)
            except Exception:  # noqa: BLE001 — fixed failure, no specialist text
                return failed
            if type(plan) is not StagePlan or plan.status != "completed" or plan.summary != SUMMARY:
                return failed
            stage = _stage_record(run, stage_id, sequence, upstream, live_versions[stage_id])
            if stage is None or stage["observed_at"] != expected:
                return failed
            stages.append(stage)
            upstream = stage["output_digest"]
        body = _canonical(_trace(run, stages)).encode("utf-8")
        if not workflow_bytes_match(body, run):
            return failed
        record = _canonical(
            {
                "attempt": run.attempt,
                "authorization_id": run.authorization_id,
                "run_id": run.run_id,
                "schema": _EXECUTION_SCHEMA,
                "scientific_validation": False,
                "session_id": run.session_id,
                "status": "completed",
            }
        ).encode("utf-8")
        return ScientificExecutionResult(
            "completed",
            (
                ExecutorArtifact("execution_record", "application/json", record),
                ExecutorArtifact("workflow_trace", "application/json", body),
            ),
        )


def workflow_bytes_match(body: bytes, run: ScientificRun) -> bool:
    """Validate a persisted v1 trace against the frozen v1 contract.

    Live supervisor constants are not consulted. A later supervisor
    release must not make an immutable v1 artifact unreadable.
    """
    if type(body) is not bytes or type(run) is not ScientificRun or not 1 <= len(body) <= 65536:
        return False
    parsed = _strict_json(body)
    if parsed is None:
        return False
    order = CONTRACT_ORDER
    frozen_versions = CONTRACT_VERSIONS
    if (
        set(parsed) != _TRACE_KEYS
        or body.decode("utf-8") != _canonical(parsed)
        or parsed["schema"] != WORKFLOW_SCHEMA
        or parsed["workflow_profile"] != WORKFLOW_PROFILE
        or parsed["workflow_version"] != WORKFLOW_VERSION
        or parsed["run_id"] != run.run_id
        or parsed["session_id"] != run.session_id
        or parsed["authorization_id"] != run.authorization_id
        or parsed["attempt"] != run.attempt
        or type(parsed["attempt"]) is not int
        or isinstance(parsed["attempt"], bool)
        or parsed["binding_hash"] != run.binding_hash
        or parsed["profile_id"] != run.profile_id
        or parsed["target_scope"] != "PDL1"
        or parsed["scientific_candidate_generated"] is not False
        or parsed["scientific_validation"] is not False
        or parsed["ranked"] is not False
        or parsed["accepted"] is not False
    ):
        return False
    stages = parsed["stages"]
    expected = workflow_observed_at(run)
    if type(stages) is not list or len(stages) != len(order) or expected is None:
        return False
    upstream = run.binding_hash
    observed = None
    for sequence, stage_id in enumerate(order, start=1):
        stage = stages[sequence - 1]
        if (
            not _stage_ok(stage, run, stage_id, sequence, upstream, frozen_versions[stage_id])
            or stage["observed_at"] != expected
        ):
            return False
        observed = stage["observed_at"]
        upstream = stage["output_digest"]
    return observed == expected


def _eligible(run: ScientificRun) -> bool:
    return (
        run.status == "running"
        and run.target_scope == "PDL1"
        and run.profile_id == _PROFILE
        and run.tool_name == "bindcraft"
        and run.budget.get("authorizes_execution") is False
        and run.binding_hash == binding_hash_for(run)
        and bool(run.binding_hash)
    )


def _trace(run: ScientificRun, stages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "accepted": False,
        "attempt": run.attempt,
        "authorization_id": run.authorization_id,
        "binding_hash": run.binding_hash,
        "profile_id": run.profile_id,
        "ranked": False,
        "run_id": run.run_id,
        "schema": WORKFLOW_SCHEMA,
        "scientific_candidate_generated": False,
        "scientific_validation": False,
        "session_id": run.session_id,
        "stages": stages,
        "target_scope": "PDL1",
        "workflow_profile": WORKFLOW_PROFILE,
        "workflow_version": WORKFLOW_VERSION,
    }


def _stage_record(
    run: ScientificRun, stage_id: str, sequence: int, upstream: str, version: str
) -> dict[str, Any] | None:
    produced = _output_document(run, stage_id, sequence, upstream, version)
    observed = workflow_observed_at(run)
    if produced is None or observed is None:
        return None
    return {**produced, "observed_at": observed, "output_digest": _digest(produced)}


def _output_document(
    run: ScientificRun, stage_id: str, sequence: int, upstream: str, version: str
) -> dict[str, Any] | None:
    if version != CONTRACT_VERSIONS.get(stage_id):
        return None
    return {
        "attempt": run.attempt,
        "implementation_id": version,
        "implementation_version": version,
        "input_digest": _digest(_input_document(run, stage_id, sequence, upstream)),
        "role": stage_id,
        "run_id": run.run_id,
        "sequence": sequence,
        "stage_id": stage_id,
        "status": "completed",
        "summary": SUMMARY,
        "upstream_digest": upstream,
    }


def _input_document(
    run: ScientificRun, stage_id: str, sequence: int, upstream: str
) -> dict[str, Any]:
    return {
        "attempt": run.attempt,
        "binding_hash": run.binding_hash,
        "run_id": run.run_id,
        "sequence": sequence,
        "stage_id": stage_id,
        "upstream_digest": upstream,
    }


def _stage_ok(
    stage: object,
    run: ScientificRun,
    stage_id: str,
    sequence: int,
    upstream: str,
    version: str,
) -> bool:
    if type(stage) is not dict or set(stage) != _STAGE_KEYS:
        return False
    produced = _output_document(run, stage_id, sequence, upstream, version)
    if produced is None:
        return False
    return (
        {key: stage[key] for key in produced} == produced
        and stage["output_digest"] == _digest(produced)
        and _stamp_ok(stage["observed_at"])
        and stage["summary"] == SUMMARY
        and _safe_text(stage["summary"])
        and type(stage["sequence"]) is int
        and not isinstance(stage["sequence"], bool)
    )


def _event_sequence(event: ScientificRunEvent) -> int | None:
    sequence = event.sequence
    if type(sequence) is not int or isinstance(sequence, bool) or sequence < 1:
        return None
    return sequence


def workflow_observed_at(run: ScientificRun) -> str | None:
    """Running-event timestamp for this attempt. None when that chain is not exact."""
    if type(run) is not ScientificRun or run.status not in {"running", "completed"}:
        return None
    events = run.events
    if type(events) is not tuple or not events:
        return None
    found: list[ScientificRunEvent] = []
    queued_text = QUEUED_TEXT if run.attempt == 1 else RETRY_QUEUED_TEXT
    for index, event in enumerate(events):
        if type(event) is not ScientificRunEvent:
            return None
        if event.status != "running" or event.attempt != run.attempt:
            continue
        previous = events[index - 1] if index else None
        queued_sequence = (
            _event_sequence(previous) if type(previous) is ScientificRunEvent else None
        )
        running_sequence = _event_sequence(event)
        if (
            type(previous) is not ScientificRunEvent
            or previous.origin != "server"
            or event.origin != "server"
            or previous.attempt != run.attempt
            or previous.status != "queued"
            or previous.text != queued_text
            or queued_sequence is None
            or running_sequence is None
            or running_sequence != queued_sequence + 1
            or not _stamp_ok(previous.recorded_at)
            or event.text != RUNNING_TEXT
            or not _stamp_ok(event.recorded_at)
            or datetime.fromisoformat(event.recorded_at)
            < datetime.fromisoformat(previous.recorded_at)
        ):
            return None
        found.append(event)
    if len(found) != 1:
        return None
    running = found[0]
    last = events[-1]
    if type(last) is not ScientificRunEvent:
        return None
    if run.status == "running":
        if last is not running or run.updated_at != running.recorded_at:
            return None
        return running.recorded_at
    completed_sequence = _event_sequence(last)
    running_sequence = _event_sequence(running)
    if (
        last.origin != "server"
        or last.status != "completed"
        or last.attempt != run.attempt
        or last.text != COMPLETED_TEXT
        or completed_sequence is None
        or running_sequence is None
        or completed_sequence != running_sequence + 1
        or not _stamp_ok(last.recorded_at)
        or run.updated_at != last.recorded_at
        or len(events) < 2
        or events[-2] is not running
        or datetime.fromisoformat(last.recorded_at) < datetime.fromisoformat(running.recorded_at)
    ):
        return None
    return running.recorded_at


def _strict_json(body: bytes) -> dict[str, Any] | None:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for key, value in items:
            if type(key) is not str or key in parsed:
                raise ValueError
            parsed[key] = value
        return parsed

    try:
        parsed = json.loads(body.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        return None
    if type(parsed) is not dict:
        return None
    return parsed
