"""Projects ScientificRunMemory into the read-only conversation port.

Reads go through the injected reader. This module has no write method.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from cellertz.application.contracts import WorkspaceSession
from cellertz.application.conversation import (
    ConversationModel,
    QueryPage,
    ReadOnlyConversation,
    ReadOnlyFact,
    SessionSnapshotQuery,
    UsageRecorder,
    require_readonly,
    show_recorded,
)
from cellertz.second_brain.episodic.store import ScientificRunMemoryReader

_MEMORY_CAPABILITIES = frozenset(
    {
        "list_candidates",
        "get_candidate",
        "get_ranking",
        "get_critique",
        "get_evidence",
        "get_artifacts",
        "get_episodic_memory",
    }
)
_RANKING_KEYS = (
    "rank",
    "ranking_signal",
    "ranking_score",
    "is_probability",
    "recommended_as_hard_filter",
)
_EVIDENCE_KEYS = (
    "ipsae_mean",
    "ipsae_min",
    "ipsae_max",
    "is_probability",
    "recommended_as_hard_filter",
    "model_aggregation",
    "n_models",
    "boltz_score",
)
_CRITIC_KEYS = (
    "critic_agreement",
    "structural_conflict",
    "missing_evidence",
    "review_flag",
)
_DECISION_KEYS = (
    "selected",
    "selection_bucket",
    "selection_order",
    "selection_reason",
)
_ARTIFACT_KEYS = ("artifact_type", "candidate_id", "uri", "sha256")
_PROVENANCE_KEYS = ("execution_id", "run_id", "tool_version", "container_digest", "seed")


class EpisodicReadOnlyQuery:
    def __init__(
        self,
        sessions: dict[str, WorkspaceSession],
        reader: ScientificRunMemoryReader,
    ) -> None:
        self._snapshot = SessionSnapshotQuery(sessions)
        self._reader = reader

    def query(
        self,
        capability: str,
        *,
        session_id: str,
        run_id: str | None = None,
        candidate_id: str | None = None,
    ) -> QueryPage:
        require_readonly(capability)
        if run_id and capability in _MEMORY_CAPABILITIES | {"get_run"}:
            link = self._link(session_id, run_id)
            if link == "mismatch":
                return QueryPage(False, (), "run_not_in_session")
            if link == "linked":
                recorded = self._reader.get_run(run_id)
                if recorded is not None:
                    document = recorded.to_document()
                    if capability == "get_run":
                        return QueryPage(True, (_run_fact(document, run_id, "get_run"),))
                    projected = _project(capability, document, run_id, candidate_id)
                    if projected.facts:
                        return projected
            elif capability == "get_run":
                return QueryPage(False, ())
        return self._snapshot.query(
            capability,
            session_id=session_id,
            run_id=run_id,
            candidate_id=candidate_id,
        )

    def _link(self, session_id: str, run_id: str) -> str:
        """Session.runs or an explicit memory session_id. Nothing else links."""
        listed = self._snapshot.query(
            "get_run",
            session_id=session_id,
            run_id=run_id,
        )
        if listed.found:
            return "linked"
        recorded = self._reader.get_run(run_id)
        if recorded is None:
            return "absent"
        if _memory_session_id(recorded.to_document()) == session_id:
            return "linked"
        return "mismatch"


def _memory_session_id(document: dict[str, Any]) -> str | None:
    value = document.get("session_id")
    if isinstance(value, str) and value.strip():
        return value
    return None


def _project(
    capability: str,
    document: dict[str, Any],
    run_id: str,
    candidate_id: str | None,
) -> QueryPage:
    if capability == "get_episodic_memory":
        return QueryPage(True, (_run_fact(document, run_id),))
    if capability in {"list_candidates", "get_candidate"}:
        return QueryPage(True, tuple(_candidate_rows(document, capability, candidate_id, ())))
    if capability == "get_ranking":
        return QueryPage(
            True,
            tuple(_candidate_rows(document, capability, candidate_id, _RANKING_KEYS)),
        )
    if capability == "get_critique":
        return QueryPage(True, tuple(_critique_facts(document, candidate_id)))
    if capability == "get_evidence":
        return QueryPage(True, tuple(_evidence_facts(document, candidate_id)))
    if capability == "get_artifacts":
        return QueryPage(True, tuple(_artifact_facts(document, candidate_id)))
    return QueryPage(found=True, facts=())


def _pairs(record: dict[str, Any], keys: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (key, show_recorded(record[key]))
        for key in keys
        if key in record and key != "composite_score"
    )


def _candidates(document: dict[str, Any], candidate_id: str | None) -> list[dict[str, Any]]:
    rows = document.get("candidates") or []
    selected = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        identity = row.get("candidate_id")
        if not isinstance(identity, str) or not identity:
            continue
        if candidate_id is not None and identity != candidate_id:
            continue
        selected.append(row)
    return selected


def _candidate_rows(
    document: dict[str, Any],
    capability: str,
    candidate_id: str | None,
    keys: tuple[str, ...],
) -> list[ReadOnlyFact]:
    facts = []
    for row in _candidates(document, candidate_id):
        attributes = _pairs(row, keys) if keys else (("candidate_id", row["candidate_id"]),)
        if not attributes:
            continue
        facts.append(
            ReadOnlyFact(
                capability=capability,
                entity_kind="candidate",
                entity_id=row["candidate_id"],
                source="episodic",
                attributes=attributes,
            )
        )
    return facts


def _critique_facts(document: dict[str, Any], candidate_id: str | None) -> list[ReadOnlyFact]:
    facts = []
    for row in _candidates(document, candidate_id):
        critic = row.get("critic")
        decision = row.get("decision")
        attributes: list[tuple[str, str]] = []
        if isinstance(critic, dict):
            attributes.extend(_pairs(critic, _CRITIC_KEYS))
        if isinstance(decision, dict):
            attributes.extend(_pairs(decision, _DECISION_KEYS))
        if not attributes:
            continue
        facts.append(
            ReadOnlyFact(
                capability="get_critique",
                entity_kind="candidate",
                entity_id=row["candidate_id"],
                source="episodic",
                attributes=tuple(attributes),
            )
        )
    return facts


def _evidence_facts(document: dict[str, Any], candidate_id: str | None) -> list[ReadOnlyFact]:
    facts = []
    for row in _candidates(document, candidate_id):
        evidence = row.get("evidence")
        if not isinstance(evidence, dict):
            continue
        attributes = _pairs(evidence, _EVIDENCE_KEYS)
        if not attributes:
            continue
        facts.append(
            ReadOnlyFact(
                capability="get_evidence",
                entity_kind="evidence",
                entity_id=row["candidate_id"],
                source="episodic",
                attributes=attributes,
            )
        )
    return facts


def _artifact_facts(document: dict[str, Any], candidate_id: str | None) -> list[ReadOnlyFact]:
    facts = []
    for artifact in document.get("source_artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        identity = artifact.get("candidate_id") or artifact.get("artifact_type")
        if candidate_id is not None and artifact.get("candidate_id") not in {None, candidate_id}:
            continue
        if not isinstance(identity, str) or not identity:
            continue
        attributes = _pairs(artifact, _ARTIFACT_KEYS)
        if not attributes:
            continue
        facts.append(
            ReadOnlyFact(
                capability="get_artifacts",
                entity_kind="artifact",
                entity_id=identity,
                source="episodic",
                attributes=attributes,
            )
        )
    execution = document.get("execution")
    if isinstance(execution, dict):
        attributes = _pairs(execution, _PROVENANCE_KEYS)
        if attributes:
            facts.append(
                ReadOnlyFact(
                    capability="get_artifacts",
                    entity_kind="provenance",
                    entity_id=str(execution.get("execution_id") or "execution"),
                    source="episodic",
                    attributes=attributes,
                )
            )
    return facts


def build_readonly_service(
    reader: ScientificRunMemoryReader,
    sessions: Mapping[str, WorkspaceSession] | None = None,
    model: ConversationModel | None = None,
    recorder: UsageRecorder | None = None,
    clock: Callable[[], datetime] | None = None,
    interaction_ids: Callable[[], str] | None = None,
) -> ReadOnlyConversation:
    """Wire a reader and a session catalog. The catalog may be empty."""

    return ReadOnlyConversation(
        EpisodicReadOnlyQuery(dict(sessions or {}), reader),
        model=model,
        recorder=recorder,
        clock=clock,
        interaction_ids=interaction_ids,
    )


def _run_fact(
    document: dict[str, Any],
    run_id: str,
    capability: str = "get_episodic_memory",
) -> ReadOnlyFact:
    keys = (
        "run_id",
        "target_id",
        "memory_house",
        "epistemic_status",
        "generator",
        "recorded_at",
    )
    return ReadOnlyFact(
        capability=capability,
        entity_kind="run",
        entity_id=run_id,
        source="episodic",
        attributes=_pairs(document, keys) or (("run_id", run_id),),
    )
