"""Record specialist stages from a saved journey execution.

This path does not launch generation. When the saved launch accepted a
candidate, ProteinGenerationAgent reads those artifacts through the
existing BindCraft adapter. It does not use fixture generation.
A read of the journey does not enter this module.
Fixture scoring metrics are not used.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cellertz.domains.protein_design.agents.critic import ProteinCriticAgentV1
from cellertz.domains.protein_design.agents.prioritization import (
    ProteinPrioritizationAgentV1,
)
from cellertz.domains.protein_design.agents.ranking import ProteinRankingAgentV1
from cellertz.tools.protein_scoring.scoring_v2 import ProteinScoreResultV2

_SPECIALISTS = ("scoring", "ranking", "critique", "prioritization")
_TERMINAL_PROGRESS = frozenset({"recorded", "failed", "interrupted"})
_LAUNCHER = "GenerationPlanService.launch_apptainer_demonstration"
_LAUNCH_TOOL = "apptainer_demonstration_launch"
_NO_CANDIDATE = (
    "No accepted candidate. Scoring, ranking, critique, and prioritization "
    "were not invoked. BindCraft i_pTM is not ipSAE and was not used as ranking_score."
)
_MISSING_ARTIFACTS = (
    "The accepted candidate does not have the required relaxed PDB and PAE artifacts. "
    "Scoring was not invoked. Fixture metrics were not used. "
    "ranking_score remains raw ipSAE. BindCraft i_pTM is not a substitute."
)
_MISSING_IDENTITY = (
    "The accepted candidate is missing the BindCraft design name or execution id "
    "needed to resolve artifacts. Scoring was not invoked. Fixture metrics were not used."
)
_ATTEMPT_MISMATCH = (
    "The candidate execution id does not match this round's attempt. "
    "Another attempt was not attached. Scoring was not invoked."
)
_INTERRUPTED = "Specialist processing was interrupted. The scoring tool was not replayed."
_SCORE_FAILED = (
    "Scoring failed before a ranking_score was recorded. "
    "Ranking, critique, and prioritization were not invoked. The scoring tool was not replayed."
)
_NOT_INVOKED = "This specialist was not invoked."
_NO_STATS = (
    "The saved launch has no final design stats. ProteinGenerationAgent was not run. "
    "The launcher was not called again. Scoring was not invoked."
)
_ORDER_MISSING = (
    "The launch did not retain residue-order evidence for the accepted model. "
    "Scoring was not invoked. Fixture metrics were not used. "
    "ranking_score remains raw ipSAE. BindCraft i_pTM is not a substitute."
)


class SpecialistProgressError(Exception):
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        super().__init__(body.get("status", "specialist_progress_rejected"))
        self.status = status
        self.body = body


def apply_specialist_progress(
    document: dict[str, Any],
    round_id: str,
    *,
    scorer: Callable[[Mapping[str, Any]], ProteinScoreResultV2] | None = None,
    save: Callable[[dict[str, Any]], None] | None = None,
    catalog_root: Path | None = None,
) -> bool:
    """Persist stage events for one round. Return whether the document changed.

    ``save`` runs after a scoring stage is marked running and before the
    scoring tool is called, so an interruption is visible and is not replayed.
    """

    target = _require_round(document, round_id)
    execution = _execution(document, target)
    if not isinstance(execution, dict) or execution.get("terminal") is not True:
        raise SpecialistProgressError(
            409,
            {
                "status": "execution_not_terminal",
                "detail": "Specialist progress reads a terminal execution. Generation was not launched.",
            },
        )
    attempt_id = execution.get("attempt_id")
    identity = _executed_plan_identity(execution)
    if not isinstance(attempt_id, str) or identity is None:
        raise SpecialistProgressError(
            409,
            {
                "status": "execution_identity_incomplete",
                "detail": "The saved execution has no attempt id or plan identity. Specialists were not invoked.",
            },
        )
    progress = target.get("specialist_progress")
    if (
        isinstance(progress, dict)
        and progress.get("attempt_id") == attempt_id
        and progress.get("status") in _TERMINAL_PROGRESS
    ):
        return False
    if _snapshot_disagrees(target, identity):
        raise SpecialistProgressError(
            409,
            {
                "status": "plan_identity_mismatch",
                "detail": (
                    "The round snapshot plan identity does not match the saved execution. "
                    "Specialists were not invoked."
                ),
            },
        )
    if _running(target):
        _interrupt(document, target, execution)
        return True
    if execution.get("accepted_candidate") is not True:
        _block_without_candidate(document, target, execution)
        return True
    generation_started = _now()
    handoff = _saved_generation(execution)
    generation_completed = _now()
    handoff["started_at"] = generation_started
    handoff["completed_at"] = generation_completed
    if handoff.get("candidate") is None:
        _block_generation(document, target, execution, handoff)
        return True
    reason, evidence = _evidence_block(execution)
    if reason is not None:
        _block_scoring(document, target, execution, reason, handoff)
        return True
    scoring_started = _now()
    _mark_scoring_running(document, target, execution, handoff, scoring_started)
    if save is not None:
        save(document)
    try:
        scored = _score(execution, scorer)
    except Exception as exc:  # noqa: BLE001 - the failure is the persisted stage outcome
        _fail_scoring(document, target, execution, exc, handoff, scoring_started, _now())
        return True
    scoring_completed = _now()
    _complete_from_score(
        document,
        target,
        execution,
        scored,
        handoff,
        evidence,
        "configured_scorer" if scorer is not None else "ProteinIpSAECalculator",
        catalog_root,
        scoring_started,
        scoring_completed,
    )
    return True


def _executed_plan_identity(execution: Mapping[str, Any]) -> dict[str, Any] | None:
    """Plan identity stored on the launch. A round snapshot is not this record."""

    plan_id = execution.get("plan_id")
    revision = execution.get("revision")
    digest = execution.get("digest")
    if not isinstance(plan_id, str) or not plan_id:
        return None
    if type(revision) is not int or isinstance(revision, bool) or revision < 1:
        return None
    if not isinstance(digest, str) or not digest:
        return None
    return {"plan_id": plan_id, "revision": revision, "digest": digest}


def _snapshot_disagrees(target: Mapping[str, Any], identity: Mapping[str, Any]) -> bool:
    plan_id = target.get("plan_id")
    revision = target.get("plan_revision")
    digest = target.get("digest")
    if isinstance(plan_id, str) and plan_id != identity["plan_id"]:
        return True
    if type(revision) is int and not isinstance(revision, bool) and revision != identity["revision"]:
        return True
    return isinstance(digest, str) and digest != identity["digest"]


def _plan_reference(execution: Mapping[str, Any], target: Mapping[str, Any]) -> dict[str, Any]:
    identity = _executed_plan_identity(execution)
    if identity is not None:
        return identity
    return {
        "plan_id": target.get("plan_id"),
        "revision": target.get("plan_revision"),
        "digest": target.get("digest"),
    }


def _require_round(document: Mapping[str, Any], round_id: str) -> dict[str, Any]:
    rounds = document.get("rounds")
    if isinstance(rounds, list):
        for item in rounds:
            if isinstance(item, dict) and item.get("round_id") == round_id:
                return item
    raise SpecialistProgressError(
        404,
        {"status": "round_absent", "detail": "The round was not found."},
    )


def _execution(document: Mapping[str, Any], target: Mapping[str, Any]) -> dict[str, Any] | None:
    if document.get("active_round_id") in {None, target.get("round_id")}:
        current = document.get("execution")
        if isinstance(current, dict):
            return current
    stored = target.get("execution")
    return stored if isinstance(stored, dict) else None


def _running(target: Mapping[str, Any]) -> bool:
    stages = target.get("specialist_stages")
    if not isinstance(stages, list):
        return False
    return any(isinstance(item, dict) and item.get("status") == "running" for item in stages)


def _interrupt(document: dict[str, Any], target: dict[str, Any], execution: Mapping[str, Any]) -> None:
    now = _now()
    stages = []
    for stage in target.get("specialist_stages") or []:
        if not isinstance(stage, dict):
            continue
        updated = dict(stage)
        if updated.get("status") == "running":
            updated["status"] = "failed"
            updated["agent_executed"] = False
            updated["completed_at"] = now
            updated["failure_reason"] = _INTERRUPTED
            updated["contribution"] = _INTERRUPTED
            updated.setdefault("implementation", "BindCraftIpSAEEvidenceProvider")
            updated.setdefault("implementation_kind", "structural_scoring")
            updated.setdefault(
                "evidence_references",
                [
                    {
                        "kind": "journey_round",
                        "journey_id": document.get("journey_id"),
                        "round_id": target.get("round_id"),
                        "attempt_id": execution.get("attempt_id"),
                        "candidate_id": None,
                        "model_id": None,
                    }
                ],
            )
            _remember(document, target, updated, execution, now)
        stages.append(updated)
    for name in _SPECIALISTS:
        if any(isinstance(item, dict) and item.get("stage_id") == name for item in stages):
            continue
        stages.append(_blocked_stage(name, _NOT_INVOKED, execution, target, document, now))
    _finish(document, target, execution, stages, "interrupted", now)


def _block_without_candidate(
    document: dict[str, Any], target: dict[str, Any], execution: Mapping[str, Any]
) -> None:
    now = _now()
    stages = [_generation_stage(document, target, execution, now)]
    for name in _SPECIALISTS:
        stages.append(_blocked_stage(name, _NO_CANDIDATE, execution, target, document, now))
    _finish(document, target, execution, stages, "recorded", now)


def _block_generation(
    document: dict[str, Any],
    target: dict[str, Any],
    execution: Mapping[str, Any],
    handoff: Mapping[str, Any],
) -> None:
    now = _now()
    stages = [_generation_stage(document, target, execution, now, handoff)]
    reason = str(handoff.get("error") or _NO_CANDIDATE)
    for name in _SPECIALISTS:
        stages.append(_blocked_stage(name, reason, execution, target, document, now))
    _finish(document, target, execution, stages, "recorded", now)


def _block_scoring(
    document: dict[str, Any],
    target: dict[str, Any],
    execution: Mapping[str, Any],
    reason: str,
    handoff: Mapping[str, Any] | None = None,
) -> None:
    now = _now()
    stages = [_generation_stage(document, target, execution, now, handoff)]
    stages.append(_blocked_stage("scoring", reason, execution, target, document, now))
    follow = "Scoring did not produce a ranking_score. This specialist was not invoked."
    for name in ("ranking", "critique", "prioritization"):
        stages.append(_blocked_stage(name, follow, execution, target, document, now))
    _finish(document, target, execution, stages, "recorded", now)


def _mark_scoring_running(
    document: dict[str, Any],
    target: dict[str, Any],
    execution: Mapping[str, Any],
    handoff: Mapping[str, Any],
    scoring_started: str,
) -> None:
    now = scoring_started
    stage = _stage(
        "scoring",
        "running",
        "structural_scoring",
        "BindCraftIpSAEEvidenceProvider",
        False,
        execution,
        target,
        document,
        now,
        contribution="Scoring started from the saved artifacts. Fixture metrics are not used.",
    )
    stage["completed_at"] = None
    _remember(document, target, stage, execution, now)
    stages = [_generation_stage(document, target, execution, now, handoff), stage]
    target["specialist_stages"] = stages
    target["specialist_progress"] = {
        "attempt_id": execution.get("attempt_id"),
        "digest": _plan_reference(execution, target)["digest"],
        "status": "running",
    }
    _publish(document, target, stages, False)


def _fail_scoring(
    document: dict[str, Any],
    target: dict[str, Any],
    execution: Mapping[str, Any],
    exc: Exception,
    handoff: Mapping[str, Any],
    scoring_started: str,
    scoring_completed: str,
) -> None:
    now = scoring_completed
    stage = _stage(
        "scoring",
        "failed",
        "structural_scoring",
        "BindCraftIpSAEEvidenceProvider",
        False,
        execution,
        target,
        document,
        now,
        contribution=_SCORE_FAILED,
        failure_reason=f"{type(exc).__name__}: {_SCORE_FAILED}",
    )
    stage["started_at"] = scoring_started
    stage["completed_at"] = scoring_completed
    _remember(document, target, stage, execution, scoring_completed)
    stages = [_generation_stage(document, target, execution, now, handoff), stage]
    follow = "Scoring failed. This specialist was not invoked."
    for name in ("ranking", "critique", "prioritization"):
        stages.append(_blocked_stage(name, follow, execution, target, document, now))
    _finish(document, target, execution, stages, "failed", now)


def _complete_from_score(
    document: dict[str, Any],
    target: dict[str, Any],
    execution: Mapping[str, Any],
    scored: ProteinScoreResultV2,
    handoff: Mapping[str, Any],
    evidence: list[dict[str, Any]],
    score_source: str,
    catalog_root: Path | None,
    scoring_started: str,
    scoring_completed: str,
) -> None:
    now = scoring_completed
    if scored.ranking_method != "ipsae" or scored.ranking_score is None:
        _block_scoring(
            document,
            target,
            execution,
            "Scoring did not return raw ipSAE. Ranking was not invoked. Fixture metrics were not used.",
            handoff,
        )
        return
    model_ids = _model_ids(scored)
    scoring = _stage(
        "scoring",
        "completed",
        "structural_scoring",
        "BindCraftIpSAEEvidenceProvider",
        True,
        execution,
        target,
        document,
        now,
        contribution=(
            f"Scoring recorded raw ipSAE for {scored.candidate_id}. "
            "This is not model synthesis and not a fixture metric. "
            "BindCraft i_pTM was not used."
        ),
        model_ids=model_ids,
    )
    scoring["output"] = {
        "candidate_id": scored.candidate_id,
        "ranking_score": scored.ranking_score,
        "ranking_signal": "ipsae",
        "model_ids": model_ids,
        "fixture_metrics": False,
        "scientific_validation_established": False,
        "experimental_validation_established": False,
        "score_source": score_source,
        "models": evidence,
    }
    recorded = _ipsae_provenance(scored, execution)
    if recorded is not None:
        scoring["output"]["ipsae_provenance"] = recorded
    previous = next(
        (
            item
            for item in target.get("specialist_stages") or []
            if isinstance(item, dict) and item.get("stage_id") == "scoring" and item.get("started_at")
        ),
        None,
    )
    scoring["started_at"] = previous["started_at"] if isinstance(previous, dict) else scoring_started
    scoring["completed_at"] = scoring_completed
    _remember(document, target, scoring, execution, scoring_completed)
    ranking_started = _now()
    ranked = ProteinRankingAgentV1().rank([scored])
    ranking_completed = _now()
    first = ranked[0]
    ranking = _stage(
        "ranking",
        "completed",
        "deterministic",
        "ProteinRankingAgentV1",
        True,
        execution,
        target,
        document,
        now,
        contribution=(
            f"Deterministic rank by raw ipSAE. {first.candidate_id} is rank {first.rank}. "
            "This is not model synthesis. BindCraft i_pTM was not used."
        ),
        model_ids=model_ids,
    )
    ranking["started_at"] = ranking_started
    ranking["completed_at"] = ranking_completed
    ranking["output"] = {
        "candidate_id": first.candidate_id,
        "rank": first.rank,
        "ranking_score": first.ranking_score,
        "ranking_signal": first.ranking_signal,
    }
    _remember(document, target, ranking, execution, ranking_completed)
    critique_started = _now()
    reviewed = ProteinCriticAgentV1().review(ranked)
    critique_completed = _now()
    critique_item = reviewed[0]
    agreement = None if critique_item.critic_assessment is None else critique_item.critic_assessment.agreement
    critique = _stage(
        "critique",
        "completed",
        "deterministic",
        "ProteinCriticAgentV1",
        True,
        execution,
        target,
        document,
        now,
        contribution=(
            f"Deterministic critique annotated {critique_item.candidate_id}. "
            f"Agreement {agreement}. Rank was not changed. Hermes was not called."
        ),
        model_ids=model_ids,
    )
    critique["started_at"] = critique_started
    critique["completed_at"] = critique_completed
    critique["output"] = {
        "candidate_id": critique_item.candidate_id,
        "rank": critique_item.rank,
        "agreement": agreement,
        "ranking_score": critique_item.ranking_score,
    }
    _remember(document, target, critique, execution, critique_completed)
    priority_started = _now()
    portfolio = ProteinPrioritizationAgentV1().select(reviewed)
    priority_completed = _now()
    chosen = next((item for item in portfolio if item.selected), None)
    if chosen is None:
        summary = "Deterministic prioritization ran and selected no candidate."
        output = {"selected": False, "candidate_id": critique_item.candidate_id}
    else:
        summary = (
            f"Deterministic prioritization selected {chosen.candidate_id} "
            f"in {chosen.selection_bucket}. {chosen.selection_reason}"
        )
        output = {
            "selected": True,
            "candidate_id": chosen.candidate_id,
            "selection_bucket": chosen.selection_bucket,
            "selection_reason": chosen.selection_reason,
        }
    prioritization = _stage(
        "prioritization",
        "completed",
        "deterministic",
        "ProteinPrioritizationAgentV1",
        True,
        execution,
        target,
        document,
        now,
        contribution=summary,
        model_ids=model_ids,
    )
    prioritization["started_at"] = priority_started
    prioritization["completed_at"] = priority_completed
    prioritization["output"] = output
    _remember(document, target, prioritization, execution, priority_completed)
    if catalog_root is not None:
        publication_started = _now()
        try:
            published = _publish_catalog(execution, catalog_root)
        except Exception as exc:  # noqa: BLE001 - publication failure keeps the completed score
            published = {
                "status": "publication_failed",
                "failure_type": type(exc).__name__,
                "scientific_validation_established": False,
                "experimental_validation_established": False,
            }
        published = dict(published)
        published["started_at"] = publication_started
        published["completed_at"] = _now()
        published["scientific_validation_established"] = False
        published["experimental_validation_established"] = False
        target["results_publication"] = published
    stages = [
        _generation_stage(document, target, execution, now, handoff),
        scoring,
        ranking,
        critique,
        prioritization,
    ]
    _finish(document, target, execution, stages, "recorded", now)


def _ipsae_provenance(
    scored: ProteinScoreResultV2,
    execution: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Copy the calculator's saved evidence. This does not score again."""

    provenance = scored.provenance if isinstance(scored.provenance, dict) else {}
    evidence = provenance.get("ipsae_evidence")
    if not isinstance(evidence, dict):
        return None
    parameters = evidence.get("protocol_parameters")
    return {
        "candidate_id": evidence.get("candidate_id"),
        "design": evidence.get("design"),
        "execution_id": evidence.get("execution_id"),
        "protocol_id": evidence.get("protocol_id"),
        "model_aggregation": evidence.get("model_aggregation"),
        "ranking_value": evidence.get("ranking_value"),
        "ipsae_mean": evidence.get("ipsae_mean"),
        "ipsae_min": evidence.get("ipsae_min"),
        "ipsae_max": evidence.get("ipsae_max"),
        "n_models": evidence.get("n_models"),
        "protocol_parameters": parameters if isinstance(parameters, dict) else {},
        "models": evidence.get("models") if isinstance(evidence.get("models"), list) else [],
        "calculation_started_at": provenance.get("calculation_started_at"),
        "calculation_completed_at": provenance.get("calculation_completed_at"),
        "calculator": provenance.get("calculator"),
        "distance_cutoff_used_by_primary_ipsae": provenance.get(
            "distance_cutoff_used_by_primary_ipsae"
        ),
        "plan_id": execution.get("plan_id"),
        "plan_revision": execution.get("revision"),
        "digest": execution.get("digest"),
        "scientific_validation_established": False,
        "experimental_validation_established": False,
    }


def _score(
    execution: Mapping[str, Any],
    scorer: Callable[[Mapping[str, Any]], ProteinScoreResultV2] | None,
) -> ProteinScoreResultV2:
    if scorer is not None:
        return scorer(execution)
    from dataclasses import replace

    from cellertz.domains.protein_design.scoring.bindcraft_ipsae import (
        BindCraftIpSAEScoreProvider,
    )
    from cellertz.tools.protein_scoring.ipsae import ProteinIpSAECalculator

    started = _now()
    provider = BindCraftIpSAEScoreProvider(
        evidence_provider=_evidence_provider(execution, ProteinIpSAECalculator()),
    )
    result = provider.score(_candidate(execution))
    completed = _now()
    provenance = dict(result.provenance)
    provenance["calculation_started_at"] = started
    provenance["calculation_completed_at"] = completed
    provenance["calculator"] = {
        "implementation": "ProteinIpSAECalculator",
        "module": "cellertz.tools.protein_scoring.ipsae",
    }
    provenance["distance_cutoff_used_by_primary_ipsae"] = False
    return replace(result, provenance=provenance)


def _saved_generation(execution: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the launcher's saved stats. Does not launch or use fixtures."""

    from cellertz.domains.protein_design.adapters.bindcraft import (
        STATS_FILENAME,
        BindCraftAdapter,
        BindCraftArtifacts,
        ExistingBindCraftArtifactsExecutor,
    )
    from cellertz.domains.protein_design.agents.generation import (
        GenerationRequest,
        ProteinGenerationAgent,
    )

    identity = execution.get("candidate_identity")
    if not isinstance(identity, dict):
        return {"candidate": None, "error": _MISSING_IDENTITY, "invoked": False}
    design = identity.get("design")
    if not isinstance(design, str) or not design:
        return {"candidate": None, "error": _MISSING_IDENTITY, "invoked": False}
    if identity.get("execution_id") != execution.get("attempt_id"):
        return {"candidate": None, "error": _ATTEMPT_MISMATCH, "invoked": False}
    if execution.get("attempt_id") == "attempt.c5c1f13e":
        return {
            "candidate": None,
            "error": (
                "attempt.c5c1f13e is recovered evidence. "
                "It was not attached to this round. ProteinGenerationAgent was not run."
            ),
            "invoked": False,
        }
    designs = _designs_for(execution)
    if designs is None or not (designs / STATS_FILENAME).is_file():
        return {"candidate": None, "error": _NO_STATS, "invoked": False}
    with (designs / STATS_FILENAME).open(encoding="utf-8", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if any((value or "").strip() for value in row.values())]
    if not rows:
        return {"candidate": None, "error": _NO_STATS, "invoked": False}
    artifacts = BindCraftArtifacts(
        output_dir=designs,
        stats_csv=designs / STATS_FILENAME,
        command=(_LAUNCH_TOOL,),
        bindcraft_version=str(execution.get("adapter_version") or "saved_launch"),
        execution_provenance={"execution_id": execution.get("attempt_id")},
    )
    agent = ProteinGenerationAgent(
        adapters={
            "bindcraft": BindCraftAdapter(
                executor=ExistingBindCraftArtifactsExecutor(artifacts),
            ),
            "fixture": _RefusingFixture(),
        }
    )
    result = agent.generate(
        GenerationRequest(
            target_id=str(identity.get("target_id") or "PDL1"),
            objective="saved-launch-normalization",
            generator="bindcraft",
            n_candidates=len(rows),
        )
    )
    if result.status != "complete":
        return {
            "candidate": None,
            "error": (
                "ProteinGenerationAgent could not normalize the saved launch. "
                "Fixture generation was not used. The launcher was not called again."
            ),
            "invoked": True,
        }
    match = next(
        (item for item in result.candidates if item.generator_metadata.get("design") == design),
        None,
    )
    if match is None:
        return {
            "candidate": None,
            "error": (
                "The saved design does not match this round. "
                "Another attempt was not attached."
            ),
            "invoked": True,
        }
    return {"candidate": match, "error": None, "invoked": True}


class _RefusingFixture:
    name = "fixture"
    version = "not_used"

    def generate(self, request: object) -> list[object]:
        del request
        raise ValueError("Fixture generation is not this journey.")


def _publish_catalog(execution: Mapping[str, Any], catalog_root: Path) -> dict[str, Any]:
    from cellertz.application.apptainer_demonstration import publish_verified_attempt_to_catalog

    attempt_id = execution.get("attempt_id")
    directory = execution.get("attempt_directory")
    if not isinstance(attempt_id, str) or not isinstance(directory, str):
        return {
            "status": "outputs_rejected",
            "reasons": ["attempt_directory_unconfigured"],
            "scientific_validation_established": False,
        }
    return publish_verified_attempt_to_catalog(Path(directory), attempt_id, catalog_root)


def _designs_for(execution: Mapping[str, Any]) -> Path | None:
    from cellertz.application.apptainer_demonstration import demonstration_designs_directory

    directory = execution.get("attempt_directory")
    attempt_id = execution.get("attempt_id")
    if not isinstance(directory, str) or not isinstance(attempt_id, str):
        return None
    return demonstration_designs_directory(Path(directory), attempt_id)


def _output_root(execution: Mapping[str, Any]) -> Path | None:
    designs = _designs_for(execution)
    if designs is None or designs.parent.name != execution.get("attempt_id"):
        return None
    return designs.parent.parent


def _evidence_provider(execution: Mapping[str, Any], calculator: Any) -> Any:
    from cellertz.domains.protein_design.evidence.bindcraft_ipsae import (
        BindCraftArtifactResolver,
        BindCraftIpSAEEvidenceProvider,
    )
    from cellertz.domains.protein_design.execution.bindcraft_scientific import (
        BINDCRAFT_IPSAE_CONFIG,
    )

    root = _output_root(execution)
    if root is None:
        raise FileNotFoundError("The saved designs directory is not this attempt.")
    return BindCraftIpSAEEvidenceProvider(
        resolver=BindCraftArtifactResolver(output_root=root),
        ipsae_config=BINDCRAFT_IPSAE_CONFIG,
        calculator=calculator,
    )


def _evidence_block(execution: Mapping[str, Any]) -> tuple[str | None, list[dict[str, Any]]]:
    identity = execution.get("candidate_identity")
    if not isinstance(identity, dict):
        return _MISSING_IDENTITY, []
    design = identity.get("design")
    execution_id = identity.get("execution_id")
    candidate_id = identity.get("candidate_id")
    if not all(isinstance(item, str) and item for item in (design, execution_id, candidate_id)):
        return _MISSING_IDENTITY, []
    if execution_id != execution.get("attempt_id"):
        return _ATTEMPT_MISMATCH, []
    root = _output_root(execution)
    if root is None:
        return _MISSING_ARTIFACTS, []
    try:
        from cellertz.domains.protein_design.evidence.bindcraft_ipsae import (
            BindCraftArtifactResolver,
        )

        resolved = BindCraftArtifactResolver(output_root=root).resolve(_candidate(execution))
    except (FileNotFoundError, TypeError, ValueError):
        return _MISSING_ARTIFACTS, []
    models: list[dict[str, Any]] = []
    for artifacts in resolved.models:
        reason, summary = _retained_model(artifacts.pdb_path, artifacts.pae_path, design, artifacts.model_number)
        if reason is not None:
            return reason, []
        models.append(summary)
    return None, models


def retained_capture_evidence(
    pdb_path: Path,
    pae_path: Path,
    design: str,
    model_number: int,
) -> tuple[str | None, dict[str, Any]]:
    """Validate one saved model against its PAE and capture-time residue order."""

    return _retained_model(pdb_path, pae_path, design, model_number)


def _retained_model(
    pdb_path: Path,
    pae_path: Path,
    design: str,
    model_number: int,
) -> tuple[str | None, dict[str, Any]]:
    import numpy as np

    sidecar = pae_path.with_name(pae_path.name.replace("_pae.npz", "_residue_order.json"))
    if not sidecar.is_file():
        return _ORDER_MISSING, {}
    try:
        record = json.loads(sidecar.read_text(encoding="utf-8"))
        with np.load(pae_path, allow_pickle=False) as payload:
            candidate = np.asarray(payload["candidate_id"])
            stored_model = np.asarray(payload["model_number"])
            pae = np.asarray(payload["pae_angstrom"])
        residues = record.get("residues")
        parsed = _pdb_ca_identity(pdb_path)
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return _ORDER_MISSING, {}
    candidate_text = candidate.item() if getattr(candidate, "shape", None) == () else None
    model_value = stored_model.item() if getattr(stored_model, "shape", None) == () else None
    if (
        record.get("established") is not True
        or record.get("contract") != "pae_index_equals_capture_time_residue_order"
        or record.get("source") != "prediction_model.aux"
        or record.get("candidate_id") != design
        or record.get("model_number") != model_number
        or candidate_text != design
        or model_value != model_number
        or not isinstance(residues, list)
        or pae.ndim != 2
        or pae.shape[0] != pae.shape[1]
        or pae.shape[0] != len(residues)
        or not _capture_matches_pdb(residues, parsed)
        or record.get("pae_sha256") != _sha256_file(pae_path)
        or record.get("pdb_sha256") != _sha256_file(pdb_path)
    ):
        return _ORDER_MISSING, {}
    return None, {
        "model_id": f"model{model_number}",
        "pdb_path": str(pdb_path),
        "pae_path": str(pae_path),
        "residue_order_path": str(sidecar),
        "ca_count": len(residues),
        "pae_sha256": record["pae_sha256"],
        "pdb_sha256": record["pdb_sha256"],
    }


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pdb_ca_identity(path: Path) -> list[dict[str, Any]]:
    residues = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith(("ATOM", "HETATM")) or len(line) < 27:
            continue
        if line[12:16].strip() != "CA":
            continue
        chain = line[21].strip()
        name = line[17:20].strip()
        if not chain or not name:
            raise ValueError("CA residue identity is incomplete")
        residues.append(
            {
                "chain": chain,
                "residue_name": name,
                "residue_number": int(line[22:26]),
                "insertion_code": line[26].strip(),
            }
        )
    return residues


def _capture_matches_pdb(captured: list[Any], parsed: list[dict[str, Any]]) -> bool:
    if len(captured) != len(parsed):
        return False
    previous = None
    number = 1
    current_chain = None
    for index, (left, right) in enumerate(zip(captured, parsed, strict=True)):
        if not isinstance(left, dict):
            return False
        chain = left.get("chain")
        model_index = left.get("model_residue_index")
        if (
            left.get("index") != index
            or not isinstance(chain, str)
            or not chain
            or type(model_index) is not int
            or type(left.get("residue_number")) is not int
            or left.get("insertion_code") != ""
            or not isinstance(left.get("residue_name"), str)
        ):
            return False
        if current_chain is None:
            current_chain = chain
            previous = model_index
        else:
            number += model_index - previous
            previous = model_index
            if chain != current_chain:
                number = 1
                current_chain = chain
        if left["residue_number"] != number:
            return False
        if (
            right["chain"] != chain
            or right["residue_name"] != left["residue_name"]
            or right["residue_number"] != number
            or right["insertion_code"] != ""
        ):
            return False
    return True


def _candidate(execution: Mapping[str, Any]) -> Any:
    from cellertz.domains.protein_design.agents.generation import NormalizedCandidate

    identity = execution["candidate_identity"]
    return NormalizedCandidate(
        candidate_id=str(identity["candidate_id"]),
        sequence=str(identity.get("sequence") or ""),
        target_id=str(identity.get("target_id") or ""),
        generator="bindcraft",
        generator_metadata={
            "design": identity["design"],
            "provenance": {"execution": {"execution_id": identity["execution_id"]}},
        },
    )


def _finish(
    document: dict[str, Any],
    target: dict[str, Any],
    execution: Mapping[str, Any],
    stages: list[dict[str, Any]],
    status: str,
    now: str,
) -> None:
    target["specialist_stages"] = stages
    target["specialist_progress"] = {
        "attempt_id": execution.get("attempt_id"),
        "digest": _plan_reference(execution, target)["digest"],
        "status": status,
        "recorded_at": now,
    }
    executed = any(stage.get("agent_executed") is True for stage in stages)
    _publish(document, target, stages, executed)


def _publish(
    document: dict[str, Any],
    target: Mapping[str, Any],
    stages: list[dict[str, Any]],
    executed: bool,
) -> None:
    if document.get("active_round_id") not in {None, target.get("round_id")}:
        return
    by_id = {stage["stage_id"]: stage for stage in stages}
    merged = []
    for stage in document.get("stages") or []:
        if not isinstance(stage, dict):
            merged.append(stage)
            continue
        replacement = by_id.pop(str(stage.get("stage_id")), None)
        merged.append(replacement if replacement is not None else stage)
    document["stages"] = merged
    if executed:
        document["specialist_agents_executed"] = True


def _recorded_interval(handoff: Mapping[str, Any] | None, now: str) -> tuple[str, str | None]:
    if not isinstance(handoff, Mapping):
        return now, None
    started = handoff.get("started_at")
    completed = handoff.get("completed_at")
    if not isinstance(started, str) or not started:
        started = now
    if not isinstance(completed, str) or not completed:
        completed = None
    return started, completed


def _generation_stage(
    document: Mapping[str, Any],
    target: Mapping[str, Any],
    execution: Mapping[str, Any],
    now: str,
    handoff: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    started, completed = _recorded_interval(handoff, now)
    candidate = None if handoff is None else handoff.get("candidate")
    if candidate is None:
        tools = [_LAUNCH_TOOL]
        adapter = execution.get("adapter_version")
        if isinstance(adapter, str) and adapter and adapter not in tools:
            tools.append(adapter)
        contribution = (
            str(handoff.get("error"))
            if handoff is not None and handoff.get("error")
            else (
                "The saved generation outcome came from the demonstration launcher. "
                "ProteinGenerationAgent did not run."
            )
        )
        invoked = bool(handoff.get("invoked")) if handoff is not None else False
        stage = _stage(
            "generation",
            "completed" if execution.get("status") != "failed" and handoff is None else "blocked",
            "adapter",
            "ProteinGenerationAgent" if invoked else _LAUNCHER,
            False,
            execution,
            target,
            document,
            started,
            contribution=contribution,
            blocking_reason=None if handoff is None else contribution,
            tools=tools,
        )
        if completed is not None:
            stage["completed_at"] = completed
        stage["output"] = {
            "attempt_id": execution.get("attempt_id"),
            "status": execution.get("status"),
            "accepted_candidate": execution.get("accepted_candidate") is True,
            "candidate_id": execution.get("candidate_id"),
            "termination_reason": execution.get("termination_reason"),
            "producer": _LAUNCHER,
            "protein_generation_agent_executed": False,
            "fixture_generation_used": False,
        }
        if handoff is None and execution.get("status") == "failed":
            stage["status"] = "failed"
        _remember(document, target, stage, execution, completed or now)
        return stage
    design = candidate.generator_metadata.get("design")
    sequence = candidate.sequence
    stage = _stage(
        "generation",
        "completed",
        "adapter",
        "ProteinGenerationAgent",
        True,
        execution,
        target,
        document,
        started,
        contribution=(
            f"ProteinGenerationAgent normalized saved design {design}. "
            f"Sequence {sequence}. Fixture generation was not used. "
            "The demonstration launcher was not called again."
        ),
        tools=["bindcraft_adapter_v1", _LAUNCH_TOOL],
    )
    if completed is not None:
        stage["completed_at"] = completed
    stage["output"] = {
        "attempt_id": execution.get("attempt_id"),
        "status": execution.get("status"),
        "accepted_candidate": True,
        "candidate_id": candidate.candidate_id,
        "design": design,
        "sequence": sequence,
        "termination_reason": execution.get("termination_reason"),
        "producer": _LAUNCHER,
        "protein_generation_agent_executed": True,
        "fixture_generation_used": False,
        "adapter": "bindcraft_adapter_v1",
    }
    _remember(document, target, stage, execution, completed or started)
    return stage


def _blocked_stage(
    name: str,
    reason: str,
    execution: Mapping[str, Any],
    target: Mapping[str, Any],
    document: Mapping[str, Any],
    now: str,
) -> dict[str, Any]:
    implementation = {
        "scoring": "BindCraftIpSAEEvidenceProvider",
        "ranking": "ProteinRankingAgentV1",
        "critique": "ProteinCriticAgentV1",
        "prioritization": "ProteinPrioritizationAgentV1",
    }[name]
    kind = "structural_scoring" if name == "scoring" else "deterministic"
    stage = _stage(
        name,
        "blocked",
        kind,
        implementation,
        False,
        execution,
        target,
        document,
        now,
        contribution=reason,
        blocking_reason=reason,
    )
    stage["started_at"] = None
    _remember(document, target, stage, execution, now)
    return stage


def _stage(
    stage_id: str,
    status: str,
    kind: str,
    implementation: str,
    executed: bool,
    execution: Mapping[str, Any],
    target: Mapping[str, Any],
    document: Mapping[str, Any],
    now: str,
    *,
    contribution: str,
    tools: list[str] | None = None,
    blocking_reason: str | None = None,
    failure_reason: str | None = None,
    model_ids: list[str] | None = None,
) -> dict[str, Any]:
    candidate = execution.get("candidate_id")
    if not isinstance(candidate, str):
        identity = execution.get("candidate_identity")
        if isinstance(identity, dict) and isinstance(identity.get("candidate_id"), str):
            candidate = identity["candidate_id"]
        else:
            candidate = None
    identity = _plan_reference(execution, target)
    return {
        "stage_id": stage_id,
        "status": status,
        "implementation_kind": kind,
        "implementation": implementation,
        "tools": tools or [],
        "agent_executed": executed,
        "specialist_recorded": True,
        "started_at": now,
        "completed_at": None if status == "running" else _now(),
        "evidence_references": [
            {
                "kind": "journey_round",
                "journey_id": document.get("journey_id"),
                "round_id": target.get("round_id"),
                "plan_id": identity["plan_id"],
                "revision": identity["revision"],
                "digest": identity["digest"],
                "attempt_id": execution.get("attempt_id"),
                "candidate_id": candidate,
                "model_id": None if not model_ids else model_ids[0],
            }
        ],
        "output": {"contribution": contribution},
        "contribution": contribution,
        "blocking_reason": blocking_reason,
        "failure_reason": failure_reason,
        "limitations": [contribution],
    }


def _remember(
    document: dict[str, Any],
    target: dict[str, Any],
    stage: Mapping[str, Any],
    execution: Mapping[str, Any],
    now: str,
) -> None:
    identity = _plan_reference(execution, target)
    sequence = int(target.get("specialist_event_sequence") or 0) + 1
    event = {
        "event_id": f"specialist:{target.get('round_id')}:{sequence}",
        "sequence": sequence,
        "at": now,
        "kind": f"stage_{stage['status']}",
        "stage_id": stage["stage_id"],
        "status": stage["status"],
        "journey_id": document.get("journey_id"),
        "round_id": target.get("round_id"),
        "plan_id": identity["plan_id"],
        "plan_revision": identity["revision"],
        "digest": identity["digest"],
        "attempt_id": execution.get("attempt_id"),
        "candidate_id": stage["evidence_references"][0]["candidate_id"],
        "model_id": stage["evidence_references"][0]["model_id"],
        "implementation": stage["implementation"],
        "implementation_kind": stage["implementation_kind"],
        "agent_executed": stage["agent_executed"],
        "reason": stage.get("blocking_reason") or stage.get("failure_reason") or stage.get("contribution"),
    }
    for owner_key, owner in (("specialist_events", target), ("specialist_events", document)):
        events = list(owner.get(owner_key) or [])
        if any(
            isinstance(item, dict)
            and item.get("stage_id") == event["stage_id"]
            and item.get("status") == event["status"]
            and item.get("attempt_id") == event["attempt_id"]
            and item.get("round_id") == event["round_id"]
            for item in events
        ):
            continue
        target["specialist_event_sequence"] = sequence
        events.append(event)
        owner[owner_key] = events


def _model_ids(scored: ProteinScoreResultV2) -> list[str]:
    evidence = scored.provenance.get("ipsae_evidence") if isinstance(scored.provenance, dict) else None
    models = evidence.get("models") if isinstance(evidence, dict) else None
    if not isinstance(models, list):
        return []
    found = []
    for item in models:
        if isinstance(item, dict) and item.get("model_number") is not None:
            found.append(str(item["model_number"]))
    return found


def _now() -> str:
    return datetime.now(UTC).isoformat()
