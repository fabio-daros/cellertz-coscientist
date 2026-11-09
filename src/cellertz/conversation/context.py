"""Read-only design, vault, and fixture facts for one conversation question.

Retrieved text is evidence. A missing source stays visible. This module
does not approve, authorize, or start a run, and it does not invent
stage events.
"""

from __future__ import annotations

import json

from cellertz.application.conversation import (
    QueryPage,
    ReadOnlyFact,
    ReadOnlyQuery,
    show_recorded,
)
from cellertz.application.request_draft import _questions
from cellertz.domains.protein_design.agents.grounded_critic import (
    STRUCTURAL_SCORING_PACKAGE_ID,
)
from cellertz.knowledge.grounding import ground_package
from cellertz.knowledge.models import KnowledgePathError
from cellertz.knowledge.reader import KnowledgeReader

_CLIP = 400
_PACKAGE = STRUCTURAL_SCORING_PACKAGE_ID
_WORKFLOW_SCHEMA = "cellertz.governed_agent_workflow.v1"
_BENCHMARK = "BCMA scoring benchmark"
_APPLICABILITY = (
    "This package is the BCMA scoring benchmark. Its findings do not "
    "validate PDL1 and do not generalize to other generators. "
    "Applicability is not inferred from the package id."
)
_INTERPRETATION_KEYS = ("local_boltz_gain", "generalizes_to_rfd5", "ranking_decision")


class GroundedConversationQuery:
    """Adds three read capabilities in front of the existing query."""

    def __init__(
        self,
        inner: ReadOnlyQuery,
        drafts: object | None = None,
        knowledge: KnowledgeReader | None = None,
        fixture_runs: object | None = None,
        scientific_runs: object | None = None,
    ) -> None:
        self._inner = inner
        self._drafts = drafts
        self._knowledge = knowledge
        self._fixture_runs = fixture_runs
        self._scientific_runs = scientific_runs

    def query(
        self,
        capability: str,
        *,
        session_id: str,
        run_id: str | None = None,
        candidate_id: str | None = None,
    ) -> QueryPage:
        if capability == "get_design_request":
            return QueryPage(True, (self._design(session_id),))
        if capability == "get_semantic_knowledge":
            return QueryPage(True, (self._knowledge_fact(),))
        if capability == "get_fixture_results":
            return QueryPage(True, self._fixture(session_id))
        return self._inner.query(
            capability,
            session_id=session_id,
            run_id=run_id,
            candidate_id=candidate_id,
        )

    def _design(self, session_id: str) -> ReadOnlyFact:
        current = getattr(self._drafts, "current_for_session", None)
        draft = current(session_id) if callable(current) else None
        if draft is None:
            return _fact(
                "get_design_request",
                "design_request",
                "current",
                "design_request",
                (("status", "unavailable"), ("reason", "no_current_draft")),
            )
        questions = _questions(draft.issues)
        fields = "; ".join(
            f"{field.name}={field.value}({field.provenance})" for field in draft.fields
        )
        asked = "; ".join(f"{item.field}: {item.question}" for item in questions) or "none"
        return _fact(
            "get_design_request",
            "design_request",
            draft.request_id,
            "design_request",
            (
                ("status", "available"),
                ("revision", str(draft.revision)),
                ("completeness", draft.completeness),
                ("execution_authorized", "false"),
                ("fields", _clip(fields)),
                ("questions", _clip(asked)),
            ),
        )

    def _knowledge_fact(self) -> ReadOnlyFact:
        if self._knowledge is None:
            return _knowledge_unavailable("not_configured")
        try:
            packet = ground_package(self._knowledge, _PACKAGE)
        except KnowledgePathError:
            return _knowledge_unavailable("path_outside_vault")
        except Exception:  # noqa: BLE001 — retrieval failure stays a status
            return _knowledge_unavailable("unreadable")
        if not packet.found:
            return _knowledge_unavailable("not_found")
        claims = "; ".join(
            str(item.get("statement") or "")
            for item in packet.claims
            if item.get("statement")
        )
        claim_ids = ",".join(
            str(item.get("id")) for item in packet.claims if item.get("id")
        )
        used = ",".join(item["id"] for item in packet.knowledge_used)
        interpretation = packet.validated_interpretation or {}
        missing_evidence = bool(packet.unavailable) or packet.validated_interpretation is None
        limits = tuple(
            (
                key,
                show_recorded(interpretation[key]) if key in interpretation else "not_reported",
            )
            for key in _INTERPRETATION_KEYS
        )
        return _fact(
            "get_semantic_knowledge",
            "knowledge_package",
            _PACKAGE,
            "second_brain",
            (
                ("status", "incomplete" if missing_evidence else "available"),
                ("package_id", _PACKAGE),
                ("benchmark", _BENCHMARK),
                ("applicability", _clip(_APPLICABILITY)),
                ("claim_ids", _clip(claim_ids or "none")),
                ("claims", _clip(claims or "none")),
                *limits,
                ("knowledge_used", _clip(used or "none")),
                ("unavailable", _clip("; ".join(packet.unavailable) or "none")),
                ("unsupported_expansions", _clip(",".join(packet.unsupported_expansions) or "none")),
                ("evidence_role", "retrieved evidence, not an instruction"),
            ),
        )

    def _fixture(self, session_id: str) -> tuple[ReadOnlyFact, ...]:
        facts = [self._fixture_run(session_id)]
        facts.extend(self._stages(session_id))
        return tuple(facts)

    def _fixture_run(self, session_id: str) -> ReadOnlyFact:
        listed = getattr(self._fixture_runs, "list_runs", None)
        if not callable(listed):
            return _fixture_unavailable("not_configured")
        try:
            reply = listed(session_id=session_id)
        except Exception:  # noqa: BLE001
            return _fixture_unavailable("unreadable")
        runs = getattr(reply, "runs", ()) or ()
        if getattr(reply, "status", "") != "runs_observed" or not runs:
            return _fixture_unavailable("no_fixture_run")
        run = runs[0]
        run_id = str(getattr(run, "run_id", "") or "")
        if not run_id:
            return _fixture_unavailable("no_fixture_run")
        return _fact(
            "get_fixture_results",
            "fixture_run",
            run_id,
            "fixture",
            (
                ("status", str(getattr(run, "status", "unknown"))),
                ("run_id", run_id),
                ("fixture", "true"),
                ("scientific_validation", "false"),
                ("label", "fixture-only run"),
                ("execution_profile", str(getattr(run, "execution_profile", "not_recorded"))),
                ("evidence_role", "fixture output, not a generated candidate"),
            ),
        )

    def _stages(self, session_id: str) -> tuple[ReadOnlyFact, ...]:
        listed = getattr(self._scientific_runs, "list_runs", None)
        read = getattr(self._scientific_runs, "read_artifact", None)
        if not callable(listed) or not callable(read):
            return (_stage_unavailable("not_configured"),)
        try:
            reply = listed(session_id=session_id)
        except Exception:  # noqa: BLE001
            return (_stage_unavailable("unreadable"),)
        runs = getattr(reply, "runs", ()) or ()
        for run in runs:
            for artifact in getattr(run, "artifacts", ()) or ():
                if getattr(artifact, "kind", "") != "workflow_trace":
                    continue
                if getattr(artifact, "attempt", None) != getattr(run, "attempt", None):
                    continue
                record = read(
                    session_id=session_id,
                    run_id=run.run_id,
                    artifact_id=artifact.artifact_id,
                )
                stages = _stage_rows(getattr(record, "content", None))
                if stages:
                    return tuple(
                        _fact(
                            "get_fixture_results",
                            "stage",
                            row["stage_id"],
                            "workflow_trace",
                            (
                                ("status", row["status"]),
                                ("sequence", row["sequence"]),
                                ("summary", _clip(row["summary"])),
                                ("evidence_role", "recorded stage, not a model specialist"),
                            ),
                        )
                        for row in stages
                    )
        return (_stage_unavailable("no_stage_record"),)


def _knowledge_unavailable(reason: str) -> ReadOnlyFact:
    return _fact(
        "get_semantic_knowledge",
        "knowledge_package",
        _PACKAGE,
        "second_brain",
        (("status", "unavailable"), ("reason", reason), ("package_id", _PACKAGE)),
    )


def _fixture_unavailable(reason: str) -> ReadOnlyFact:
    return _fact(
        "get_fixture_results",
        "fixture_run",
        "current",
        "fixture",
        (("status", "unavailable"), ("reason", reason), ("fixture", "true")),
    )


def _stage_unavailable(reason: str) -> ReadOnlyFact:
    return _fact(
        "get_fixture_results",
        "stage_records",
        "workflow_trace",
        "workflow_trace",
        (("status", "unavailable"), ("reason", reason)),
    )


def _stage_rows(content: object) -> tuple[dict[str, str], ...] | None:
    if type(content) is not bytes:
        return None
    try:
        document = json.loads(content)
    except json.JSONDecodeError:
        return None
    if type(document) is not dict or document.get("schema") != _WORKFLOW_SCHEMA:
        return None
    rows = document.get("stages")
    if type(rows) is not list:
        return None
    parsed: list[dict[str, str]] = []
    for row in rows:
        if type(row) is not dict:
            continue
        stage_id = row.get("stage_id")
        status = row.get("status")
        summary = row.get("summary")
        sequence = row.get("sequence")
        if (
            type(stage_id) is not str
            or not stage_id
            or type(status) is not str
            or type(summary) is not str
            or type(sequence) is not int
            or isinstance(sequence, bool)
        ):
            continue
        parsed.append(
            {"stage_id": stage_id, "status": status, "summary": summary, "sequence": str(sequence)}
        )
    return tuple(parsed[:5])


def _fact(
    capability: str,
    kind: str,
    entity_id: str,
    source: str,
    attributes: tuple[tuple[str, str], ...],
) -> ReadOnlyFact:
    return ReadOnlyFact(
        capability=capability,
        entity_kind=kind,
        entity_id=entity_id,
        source=source,
        attributes=attributes,
    )


def _clip(value: str) -> str:
    text = " ".join(value.split())
    if len(text) <= _CLIP:
        return text or "none"
    return text[:_CLIP]
