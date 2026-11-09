"""Read-only join from a Results review to one saved specialist record."""

from __future__ import annotations

from typing import Any

from cellertz.domains.protein_design.adapters.bindcraft import (
    BindCraftError,
    candidate_id_for_design,
)

_AGGREGATION_FIELDS = (
    "model_aggregation",
    "within_model_aggregation",
    "protocol_id",
    "pae_cutoff",
    "distance_cutoff",
    "chain_a",
    "chain_b",
)
_STAGE_ORDER = ("scoring", "ranking", "critique", "prioritization")


def project_saved_specialist_evidence(
    *,
    attempt_id: str,
    design: str,
    review_models: dict[str, str],
    documents: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Join one catalog attempt to saved execution and specialist stages.

    The review stays the source of recorded metrics. This function does not
    score, publish, or write a journey.
    """

    if documents is None:
        return _absent("The inspection did not read a journey store.")
    candidate_id = _candidate_id(design)
    if candidate_id is None:
        return _status(
            "mismatched",
            "The published design does not produce a BindCraft candidate id.",
            candidate_id=None,
        )
    views = _views(documents, attempt_id)
    if not views:
        return _absent("No saved journey record matches this attempt.")
    if len(views) != 1 or any(item.get("ambiguous") for item in views):
        return _status(
            "ambiguous",
            "More than one saved execution matches this attempt.",
            candidate_id=candidate_id,
        )
    view = views[0]
    execution = view["execution"]
    problems = _execution_problems(execution, attempt_id, design, candidate_id)
    if problems:
        return _status(
            "mismatched",
            "Saved execution identity does not match this result: " + ", ".join(problems) + ".",
            candidate_id=candidate_id,
        )
    stages = _matching_stages(view["stages"], execution, attempt_id, candidate_id)
    superseded = _superseded(view["corrections"], attempt_id)
    if view["stages"] and not stages:
        body = _status(
            "mismatched",
            "Saved specialist stages do not cite this execution's plan revision and digest.",
            candidate_id=candidate_id,
        )
        body["journey_id"] = view.get("journey_id")
        body["round_id"] = view.get("round_id")
        body["plan_id"] = execution.get("plan_id")
        body["plan_revision"] = execution.get("revision")
        body["digest"] = execution.get("digest")
        body["superseded"] = superseded
        body["attempted_trajectory_budget"] = _budget(execution)
        return body
    duplicates = [
        stage_id
        for stage_id in _STAGE_ORDER
        if sum(stage.get("stage_id") == stage_id for stage in stages) > 1
    ]
    if duplicates:
        body = _status(
            "ambiguous",
            "More than one saved specialist stage matches this attempt.",
            candidate_id=candidate_id,
        )
        body["superseded"] = superseded
        return body
    scoring = _one_stage(stages, "scoring")
    if scoring is None:
        body = _linked_shell(view, execution, candidate_id, superseded)
        body["status"] = "not_recorded"
        body["detail"] = (
            "The saved execution matches this attempt. Current specialist scoring "
            "is not recorded. Superseded processing is not the current score."
        )
        body["ranking_score"] = "not_recorded"
        body["stages"] = _public_stages(stages)
        return body
    output = scoring.get("output") if isinstance(scoring.get("output"), dict) else {}
    if scoring.get("status") != "completed" or scoring.get("agent_executed") is not True:
        body = _linked_shell(view, execution, candidate_id, superseded)
        body["status"] = "recorded_without_score"
        body["detail"] = "Scoring did not run. No ranking_score was calculated."
        body["ranking_score"] = "not_recorded"
        body["stages"] = _public_stages(stages)
        return body
    model_problems = _model_problems(output, review_models)
    if model_problems:
        body = _status(
            "mismatched",
            "Saved model artifact identities do not match this review: "
            + ", ".join(model_problems)
            + ".",
            candidate_id=candidate_id,
        )
        body["superseded"] = superseded
        return body
    score = output.get("ranking_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        body = _linked_shell(view, execution, candidate_id, superseded)
        body["status"] = "mismatched"
        body["detail"] = "The saved scoring record has no candidate ranking_score."
        body["ranking_score"] = "not_recorded"
        return body
    body = _linked_shell(view, execution, candidate_id, superseded)
    body["status"] = "linked"
    body["detail"] = (
        "The saved execution and specialist record match this attempt, candidate, "
        "plan revision, digest, and model artifacts."
    )
    body["ranking_score"] = score
    body["original_ranking_score"] = score
    body["score_source"] = _text(output.get("score_source"))
    body["ranking_signal"] = _text(output.get("ranking_signal"))
    body["model_ids"] = _model_ids(output)
    body["artifact_model_ids"] = sorted(review_models)
    body["per_model_ranking_scores"] = "not_recorded"
    body["applies_to_each_model"] = False
    _apply_recorded_provenance(body, output)
    _apply_audits(body, view)
    body["stages"] = _public_stages(stages)
    _add_later_stages(body, stages)
    return body


def _apply_recorded_provenance(body: dict[str, Any], output: dict[str, Any]) -> None:
    recorded = output.get("ipsae_provenance")
    if not isinstance(recorded, dict):
        body["ipsae_provenance"] = "not_recorded"
        return
    body["ipsae_provenance"] = recorded
    parameters = recorded.get("protocol_parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    body["aggregation"] = {
        "model_aggregation": recorded.get("model_aggregation", "not_recorded"),
        "within_model_aggregation": parameters.get("within_model_aggregation", "not_recorded"),
        "protocol_id": recorded.get("protocol_id", "not_recorded"),
        "pae_cutoff": parameters.get("pae_cutoff", "not_recorded"),
        "distance_cutoff": parameters.get("distance_cutoff", "not_recorded"),
        "chain_a": parameters.get("chain_a", "not_recorded"),
        "chain_b": parameters.get("chain_b", "not_recorded"),
    }
    models = recorded.get("models")
    if not isinstance(models, list):
        return
    body["per_model_ranking_scores"] = [
        {
            "model_number": item.get("model_number"),
            "ipsae": item.get("ipsae"),
            "ipsae_ab": item.get("ipsae_ab"),
            "ipsae_ba": item.get("ipsae_ba"),
            "pdb_sha256": item.get("pdb_sha256"),
            "pae_sha256": item.get("pae_sha256"),
            "residue_order_sha256": item.get("residue_order_sha256"),
        }
        for item in models
        if isinstance(item, dict)
    ]
    body["applies_to_each_model"] = False


def _apply_audits(body: dict[str, Any], view: dict[str, Any]) -> None:
    audits = view.get("scoring_audits")
    if not isinstance(audits, list) or not audits:
        body["scoring_audit"] = {"status": "not_recorded"}
        return
    body["scoring_audit"] = {
        "status": "recorded",
        "records": audits,
        "replaces_original_ranking_score": False,
    }


def _candidate_id(design: str) -> str | None:
    try:
        return candidate_id_for_design(design)
    except BindCraftError:
        return None


def _views(documents: list[dict[str, Any]], attempt_id: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for document in documents:
        if not isinstance(document, dict):
            continue
        rounds = [item for item in document.get("rounds") or [] if isinstance(item, dict)]
        execution = document.get("execution")
        doc_exec = execution if isinstance(execution, dict) else None
        claimed: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for target in rounds:
            snapshot = target.get("execution")
            if isinstance(snapshot, dict) and snapshot.get("attempt_id") == attempt_id:
                claimed.append((target, snapshot))
        if len(claimed) > 1:
            found.append({"ambiguous": True})
            continue
        if len(claimed) == 1:
            target, snapshot = claimed[0]
            found.append(_view(document, target, snapshot))
            continue
        if doc_exec is None or doc_exec.get("attempt_id") != attempt_id:
            continue
        chosen = [
            item for item in rounds if item.get("round_id") == document.get("active_round_id")
        ]
        if not chosen and len(rounds) == 1:
            chosen = rounds
        if len(chosen) != 1:
            found.append({"ambiguous": True})
            continue
        found.append(_view(document, chosen[0], doc_exec))
    return found


def _view(
    document: dict[str, Any],
    target: dict[str, Any],
    execution: dict[str, Any],
) -> dict[str, Any]:
    stages = target.get("specialist_stages")
    corrections = target.get("outcome_corrections")
    return {
        "ambiguous": False,
        "journey_id": document.get("journey_id"),
        "round_id": target.get("round_id"),
        "execution": execution,
        "stages": [item for item in stages or [] if isinstance(item, dict)],
        "corrections": [item for item in corrections or [] if isinstance(item, dict)],
        "scoring_audits": [
            item for item in target.get("scoring_audits") or [] if isinstance(item, dict)
        ],
    }


def _execution_problems(
    execution: dict[str, Any],
    attempt_id: str,
    design: str,
    candidate_id: str,
) -> list[str]:
    problems: list[str] = []
    if execution.get("attempt_id") != attempt_id:
        problems.append("attempt")
    if execution.get("candidate_id") != candidate_id:
        problems.append("candidate_id")
    identity = execution.get("candidate_identity")
    if (
        not isinstance(identity, dict)
        or identity.get("design") != design
        or identity.get("candidate_id") != candidate_id
        or identity.get("execution_id") != attempt_id
    ):
        problems.append("candidate_identity")
    plan_id = execution.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        problems.append("plan")
    revision = execution.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int):
        problems.append("revision")
    digest = execution.get("digest")
    if not isinstance(digest, str) or not digest:
        problems.append("digest")
    return problems


def _matching_stages(
    stages: list[dict[str, Any]],
    execution: dict[str, Any],
    attempt_id: str,
    candidate_id: str,
) -> list[dict[str, Any]]:
    return [
        stage
        for stage in stages
        if _reference_matches(stage, execution, attempt_id, candidate_id)
    ]


def _reference_matches(
    stage: dict[str, Any],
    execution: dict[str, Any],
    attempt_id: str,
    candidate_id: str,
) -> bool:
    references = stage.get("evidence_references")
    if not isinstance(references, list):
        return False
    for reference in references:
        if not isinstance(reference, dict):
            continue
        if (
            reference.get("attempt_id") == attempt_id
            and reference.get("candidate_id") == candidate_id
            and reference.get("plan_id") == execution.get("plan_id")
            and reference.get("revision") == execution.get("revision")
            and reference.get("digest") == execution.get("digest")
        ):
            return True
    return False


def _one_stage(stages: list[dict[str, Any]], stage_id: str) -> dict[str, Any] | None:
    found = [stage for stage in stages if stage.get("stage_id") == stage_id]
    if len(found) != 1:
        return None
    return found[0]


def _model_problems(output: dict[str, Any], review_models: dict[str, str]) -> list[str]:
    recorded = output.get("models")
    if not isinstance(recorded, list) or not review_models:
        return ["model_artifacts"]
    found: dict[str, Any] = {}
    for item in recorded:
        if isinstance(item, dict) and isinstance(item.get("model_id"), str):
            found[item["model_id"]] = item.get("pdb_sha256")
    problems = [
        model_id
        for model_id, digest in review_models.items()
        if found.get(model_id) != digest
    ]
    if any(model_id not in review_models for model_id in found):
        problems.append("unexpected_model")
    return problems


def _model_ids(output: dict[str, Any]) -> list[str] | str:
    recorded = output.get("model_ids")
    if not isinstance(recorded, list) or not all(isinstance(item, str) for item in recorded):
        return "not_recorded"
    return list(recorded)


def _add_later_stages(body: dict[str, Any], stages: list[dict[str, Any]]) -> None:
    ranking = _one_stage(stages, "ranking")
    critique = _one_stage(stages, "critique")
    priority = _one_stage(stages, "prioritization")
    rank_output = ranking.get("output") if isinstance(ranking, dict) else None
    rank = rank_output.get("rank") if isinstance(rank_output, dict) else None
    if (
        isinstance(ranking, dict)
        and ranking.get("status") == "completed"
        and ranking.get("agent_executed") is True
        and isinstance(rank, int)
        and not isinstance(rank, bool)
    ):
        body["rank"] = rank
        body["rank_note"] = (
            f"Rank {rank} among 1 recorded candidate. "
            "This does not establish comparative superiority."
        )
    critique_output = critique.get("output") if isinstance(critique, dict) else None
    if isinstance(critique_output, dict) and critique.get("agent_executed") is True:
        agreement = critique_output.get("agreement")
        body["critique_agreement"] = "unknown" if agreement is None else agreement
        body["critique_kind"] = "deterministic"
    priority_output = priority.get("output") if isinstance(priority, dict) else None
    if isinstance(priority_output, dict) and priority.get("agent_executed") is True:
        body["prioritization_bucket"] = _text(priority_output.get("selection_bucket"))
        body["prioritization_reason"] = _text(priority_output.get("selection_reason"))
        body["prioritization_kind"] = "deterministic"


def _public_stages(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = []
    for stage_id in _STAGE_ORDER:
        stage = _one_stage(stages, stage_id)
        if stage is not None:
            ordered.append(_public_stage(stage))
    return ordered


def _public_stage(stage: dict[str, Any]) -> dict[str, Any]:
    references = stage.get("evidence_references")
    public_refs = [item for item in references or [] if isinstance(item, dict)]
    return {
        "stage_id": stage.get("stage_id"),
        "status": stage.get("status"),
        "agent_executed": stage.get("agent_executed") is True,
        "implementation": stage.get("implementation"),
        "implementation_kind": stage.get("implementation_kind"),
        "evidence_references": public_refs,
    }


def _superseded(corrections: list[dict[str, Any]], attempt_id: str) -> list[dict[str, Any]]:
    historical = []
    for item in corrections:
        if item.get("attempt_id") != attempt_id:
            continue
        stages = item.get("superseded_specialist_stages")
        recorded = [stage for stage in stages or [] if isinstance(stage, dict)]
        score = "not_recorded"
        for stage in recorded:
            if stage.get("stage_id") != "scoring":
                continue
            output = stage.get("output")
            score_value = output.get("ranking_score") if isinstance(output, dict) else None
            if isinstance(score_value, (int, float)) and not isinstance(score_value, bool):
                score = score_value
        historical.append(
            {
                "kind": item.get("kind"),
                "status": "superseded",
                "used_as_current_score": False,
                "stage_ids": [stage.get("stage_id") for stage in recorded],
                "ranking_score": score,
            }
        )
    return historical


def _linked_shell(
    view: dict[str, Any],
    execution: dict[str, Any],
    candidate_id: str,
    superseded: list[dict[str, Any]],
) -> dict[str, Any]:
    body = _status("linked", "", candidate_id=candidate_id)
    body["journey_id"] = view.get("journey_id")
    body["round_id"] = view.get("round_id")
    body["plan_id"] = execution.get("plan_id")
    body["plan_revision"] = execution.get("revision")
    body["digest"] = execution.get("digest")
    body["superseded"] = superseded
    body["attempted_trajectory_budget"] = _budget(execution)
    body["stages"] = _public_stages([])
    _apply_audits(body, view)
    return body


def _budget(execution: dict[str, Any]) -> dict[str, Any]:
    flag = execution.get("budget_compliance_established")
    usage = execution.get("budget_usage")
    requested = usage.get("requested_attempted_trajectories") if isinstance(usage, dict) else None
    reason = execution.get("termination_reason")
    recorded = isinstance(flag, bool)
    return {
        "status": "recorded" if recorded else "not_recorded",
        "compliance_established": flag if recorded else None,
        "requested_attempted_trajectories": (
            requested if isinstance(requested, int) and not isinstance(requested, bool) else None
        ),
        "termination_reason": reason if isinstance(reason, str) else None,
        "establishes_execution_compliance": False,
        "missing_is_violation": False,
    }


def _absent(detail: str) -> dict[str, Any]:
    return _status("not_recorded", detail, candidate_id=None)


def _status(status: str, detail: str, *, candidate_id: str | None) -> dict[str, Any]:
    return {
        "status": status,
        "detail": detail,
        "journey_id": None,
        "round_id": None,
        "plan_id": None,
        "plan_revision": None,
        "digest": None,
        "candidate_id": candidate_id,
        "ranking_score": "not_recorded",
        "ranking_score_scope": "candidate",
        "applies_to_each_model": False,
        "per_model_ranking_scores": "not_recorded",
        "score_source": "not_recorded",
        "ranking_signal": "not_recorded",
        "model_ids": "not_recorded",
        "artifact_model_ids": [],
        "aggregation": {field: "not_recorded" for field in _AGGREGATION_FIELDS},
        "rank": "not_recorded",
        "rank_note": "Rank is not recorded. This does not establish comparative superiority.",
        "critique_agreement": "not_recorded",
        "critique_kind": "not_recorded",
        "prioritization_bucket": "not_recorded",
        "prioritization_reason": "not_recorded",
        "prioritization_kind": "not_recorded",
        "stages": [],
        "superseded": [],
        "attempted_trajectory_budget": {
            "status": "not_recorded",
            "compliance_established": None,
            "requested_attempted_trajectories": None,
            "termination_reason": None,
            "establishes_execution_compliance": False,
            "missing_is_violation": False,
        },
        "pdb_integrity_is_execution_compliance": False,
        "budget_establishes_execution_compliance": False,
        "scientific_validation_established": False,
        "experimental_validation_established": False,
        "original_ranking_score": "not_recorded",
        "ipsae_provenance": "not_recorded",
        "scoring_audit": {"status": "not_recorded"},
    }


def _text(value: Any) -> Any:
    if isinstance(value, str) and value:
        return value
    return "not_recorded"
