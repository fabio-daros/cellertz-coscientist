"""CPU-only ipSAE audit of one saved attempt.

The absolute tolerance below compares a recalculated candidate ranking_score
with the original persisted ranking_score. It is a numerical comparison
tolerance, not a scientific acceptance threshold and not experimental validation.

A GET must not call this module. An interrupted audit is not recalculated
unless the operator explicitly resumes it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from cellertz.domains.protein_design.adapters.bindcraft import (
    BindCraftError,
    candidate_id_for_design,
)
from cellertz.domains.protein_design.execution.bindcraft_scientific import (
    BINDCRAFT_IPSAE_CONFIG,
)

AUDIT_ABSOLUTE_TOLERANCE = 1e-9


def apply_scoring_audit(
    document: dict[str, Any],
    attempt_id: str,
    *,
    resume_interrupted: bool = False,
    save: Callable[[dict[str, Any]], None] | None = None,
    calculator: Any | None = None,
) -> tuple[int, dict[str, Any]]:
    """Verify artifacts, then score with the existing calculator.

    Specialist stages, ranking, critique, prioritization, and review files
    are left as they were.
    """

    found = _round_for_attempt(document, attempt_id)
    if found is None:
        return 404, {"status": "attempt_absent", "detail": "No saved round matches this attempt."}
    if found == "ambiguous":
        return 409, {
            "status": "attempt_ambiguous",
            "detail": "More than one saved round matches this attempt. The audit was not run.",
        }
    target, execution = found
    stages_before = json.dumps(target.get("specialist_stages"), sort_keys=True)
    publication_before = json.dumps(target.get("results_publication"), sort_keys=True)
    verified = _verify(target, execution)
    if verified["status"] != "ok":
        record = _existing(target, verified.get("audit_key"))
        if isinstance(record, dict) and record.get("status") == "verification_failed":
            return 200, {"status": "idempotent", "scoring_audit": _public(record)}
        record = _new_record(execution, verified, "verification_failed")
        _store(target, record)
        _guard(target, stages_before, publication_before)
        return 409, {"status": "verification_failed", "scoring_audit": _public(record)}
    key = str(verified["audit_key"])
    current = _existing(target, key)
    if isinstance(current, dict) and current.get("status") == "completed":
        return 200, {"status": "idempotent", "scoring_audit": _public(current)}
    if isinstance(current, dict) and current.get("status") in {"running", "interrupted"}:
        if not resume_interrupted:
            return 409, {
                "status": "audit_interrupted",
                "detail": "An interrupted audit was not replayed.",
                "scoring_audit": _public(current),
            }
        record = current
    else:
        record = _new_record(execution, verified, "running")
        _store(target, record)
    record["status"] = "running"
    record["calculation_started_at"] = _now()
    record.pop("failure", None)
    if save is not None:
        save(document)
    try:
        evidence = _calculate(execution, calculator)
    except Exception as exc:  # noqa: BLE001 - interruption is the persisted audit state
        record["status"] = "interrupted"
        record["failure"] = type(exc).__name__
        _guard(target, stages_before, publication_before)
        if save is not None:
            save(document)
        return 409, {
            "status": "audit_interrupted",
            "detail": "The audit stopped before completion and was not replayed.",
            "scoring_audit": _public(record),
        }
    _finish(record, evidence, verified["original_ranking_score"])
    _guard(target, stages_before, publication_before)
    return 200, {"status": "completed", "scoring_audit": _public(record)}


def _calculate(execution: dict[str, Any], calculator: Any | None) -> Any:
    from cellertz.application.journey_specialist_progress import _candidate, _evidence_provider
    from cellertz.tools.protein_scoring.ipsae import ProteinIpSAECalculator

    candidate = _candidate(execution)
    if calculator is not None:
        return calculator.evaluate(candidate)
    provider = _evidence_provider(execution, ProteinIpSAECalculator())
    return provider.evaluate(candidate)


def _verify(target: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    from cellertz.application.apptainer_demonstration import demonstration_designs_directory
    from cellertz.application.journey_specialist_progress import retained_capture_evidence

    problems: list[str] = []
    attempt_id = execution.get("attempt_id")
    directory = execution.get("attempt_directory")
    identity = execution.get("candidate_identity")
    if not isinstance(attempt_id, str) or not isinstance(directory, str):
        problems.append("attempt_directory")
    if not isinstance(identity, dict):
        problems.append("candidate_identity")
        design = ""
        candidate_id = ""
    else:
        design = identity.get("design") if isinstance(identity.get("design"), str) else ""
        candidate_id = identity.get("candidate_id") if isinstance(identity.get("candidate_id"), str) else ""
        try:
            expected = candidate_id_for_design(design) if design else ""
        except BindCraftError:
            expected = ""
        if not design or candidate_id != expected or identity.get("execution_id") != attempt_id:
            problems.append("candidate_identity")
        if execution.get("candidate_id") != expected:
            problems.append("candidate_id")
    plan_id = execution.get("plan_id")
    revision = execution.get("revision")
    digest = execution.get("digest")
    if not isinstance(plan_id, str) or type(revision) is not int or not isinstance(digest, str):
        problems.append("plan_identity")
    original = _original_from_round(target)
    if problems:
        return _failed(problems, original)
    assert isinstance(directory, str) and isinstance(attempt_id, str)
    designs = demonstration_designs_directory(Path(directory), attempt_id)
    if designs is None:
        return _failed(["designs_directory"], original)
    review_path = Path(directory) / "review.json"
    if not review_path.is_file():
        return _failed(["review_missing"], original)
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _failed(["review_unreadable"], original)
    if review.get("design") != design or review.get("attempt_id") != attempt_id:
        return _failed(["review_identity"], original)
    review_models = review.get("models") if isinstance(review.get("models"), dict) else {}
    from cellertz.domains.protein_design.evidence.bindcraft_ipsae import BindCraftArtifactResolver

    output_root = designs.parent.parent if designs.parent.name == attempt_id else None
    if output_root is None:
        return _failed(["designs_directory"], original)
    try:
        from cellertz.application.journey_specialist_progress import _candidate

        resolved = BindCraftArtifactResolver(output_root=output_root).resolve(_candidate(execution))
    except (FileNotFoundError, TypeError, ValueError):
        return _failed(["model_artifacts"], original)
    models: list[dict[str, Any]] = []
    for artifacts in resolved.models:
        reason, summary = retained_capture_evidence(
            artifacts.pdb_path, artifacts.pae_path, design, artifacts.model_number
        )
        if reason is not None:
            return _failed(
                [
                    reason,
                    hashlib.sha256(artifacts.pdb_path.read_bytes()).hexdigest(),
                    hashlib.sha256(artifacts.pae_path.read_bytes()).hexdigest(),
                ],
                original,
            )
        sidecar = artifacts.pae_path.with_name(
            artifacts.pae_path.name.replace("_pae.npz", "_residue_order.json")
        )
        if not sidecar.is_file():
            return _failed(["residue_order_missing"], original)
        model_id = f"model{artifacts.model_number}"
        review_model = review_models.get(model_id)
        review_hash = review_model.get("sha256") if isinstance(review_model, dict) else None
        if review_hash != summary.get("pdb_sha256"):
            observed = summary.get("pdb_sha256")
            return _failed([f"{model_id}_pdb_hash:{observed}"], original)
        models.append(
            {
                "model_number": artifacts.model_number,
                "model_id": model_id,
                "pdb_sha256": summary.get("pdb_sha256"),
                "pae_sha256": summary.get("pae_sha256"),
                "residue_order_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(),
            }
        )
    if not models:
        return _failed(["model_artifacts"], original)
    payload = {
        "attempt_id": attempt_id,
        "candidate_id": execution.get("candidate_id"),
        "plan_id": plan_id,
        "revision": revision,
        "digest": digest,
        "protocol_id": BINDCRAFT_IPSAE_CONFIG.protocol_id,
        "pae_cutoff": BINDCRAFT_IPSAE_CONFIG.pae_cutoff,
        "distance_cutoff": BINDCRAFT_IPSAE_CONFIG.distance_cutoff,
        "chain_a": BINDCRAFT_IPSAE_CONFIG.chain_a,
        "chain_b": BINDCRAFT_IPSAE_CONFIG.chain_b,
        "within_model_aggregation": BINDCRAFT_IPSAE_CONFIG.aggregation,
        "original_ranking_score": original,
        "models": models,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "status": "ok",
        "problems": [],
        "audit_key": hashlib.sha256(encoded).hexdigest(),
        "original_ranking_score": original,
        "identity": payload,
        "models": models,
    }


def _failed(problems: list[str], original: float | None) -> dict[str, Any]:
    encoded = json.dumps(problems, sort_keys=True).encode("utf-8")
    return {
        "status": "failed",
        "problems": problems,
        "audit_key": hashlib.sha256(encoded).hexdigest(),
        "original_ranking_score": original,
    }


def _original_from_round(target: dict[str, Any]) -> float | None:
    stages = [item for item in target.get("specialist_stages") or [] if isinstance(item, dict)]
    scoring = [item for item in stages if item.get("stage_id") == "scoring"]
    if len(scoring) != 1:
        return None
    output = scoring[0].get("output")
    if not isinstance(output, dict):
        return None
    score = output.get("ranking_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    return float(score)


def _round_for_attempt(
    document: dict[str, Any], attempt_id: str
) -> tuple[dict[str, Any], dict[str, Any]] | None | str:
    rounds = [item for item in document.get("rounds") or [] if isinstance(item, dict)]
    execution = document.get("execution")
    doc_exec = execution if isinstance(execution, dict) else None
    found: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for target in rounds:
        snapshot = target.get("execution") if isinstance(target.get("execution"), dict) else None
        if isinstance(snapshot, dict) and snapshot.get("attempt_id") == attempt_id:
            found.append((target, snapshot))
            continue
        if (
            doc_exec is not None
            and doc_exec.get("attempt_id") == attempt_id
            and document.get("active_round_id") == target.get("round_id")
        ):
            found.append((target, doc_exec))
    if len(found) > 1:
        return "ambiguous"
    if len(found) == 1:
        target, execution = found[0]
        return target, execution
    return None


def _new_record(execution: dict[str, Any], verified: dict[str, Any], status: str) -> dict[str, Any]:
    config = BINDCRAFT_IPSAE_CONFIG
    return {
        "audit_id": f"audit_{uuid4().hex}",
        "status": status,
        "audit_key": verified.get("audit_key"),
        "attempt_id": execution.get("attempt_id"),
        "candidate_id": execution.get("candidate_id"),
        "plan_id": execution.get("plan_id"),
        "plan_revision": execution.get("revision"),
        "digest": execution.get("digest"),
        "problems": verified.get("problems") or [],
        "original_ranking_score": verified.get("original_ranking_score"),
        "recalculated_ranking_score": "not_recorded",
        "absolute_tolerance": AUDIT_ABSOLUTE_TOLERANCE,
        "absolute_difference": "not_recorded",
        "agrees": None,
        "protocol_id": config.protocol_id,
        "protocol_parameters": {
            "pae_cutoff": config.pae_cutoff,
            "distance_cutoff": config.distance_cutoff,
            "chain_a": config.chain_a,
            "chain_b": config.chain_b,
            "within_model_aggregation": config.aggregation,
            "distance_cutoff_used_by_primary_ipsae": False,
        },
        "models": verified.get("models") or [],
        "replaces_specialist_record": False,
        "scientific_validation_established": False,
        "experimental_validation_established": False,
        "calculation_started_at": None,
        "calculation_completed_at": None,
    }


def _finish(record: dict[str, Any], evidence: Any, original: float | None) -> None:
    record["status"] = "completed"
    record["calculation_completed_at"] = _now()
    record["recalculated_ranking_score"] = evidence.ranking_value
    record["model_aggregation"] = evidence.model_aggregation
    record["ipsae_mean"] = evidence.ipsae_mean
    record["ipsae_min"] = evidence.ipsae_min
    record["ipsae_max"] = evidence.ipsae_max
    record["calculated_models"] = [
        {
            "model_number": model.model_number,
            "ipsae": model.ipsae,
            "ipsae_ab": model.ipsae_ab,
            "ipsae_ba": model.ipsae_ba,
            "pdb_sha256": model.pdb_sha256,
            "pae_sha256": model.pae_sha256,
            "residue_order_sha256": model.residue_order_sha256,
        }
        for model in evidence.models
    ]
    if original is None:
        record["agrees"] = None
        record["absolute_difference"] = "not_recorded"
        record["agreement"] = "not_comparable"
        return
    difference = abs(float(evidence.ranking_value) - original)
    record["absolute_difference"] = difference
    record["agrees"] = difference <= AUDIT_ABSOLUTE_TOLERANCE
    record["agreement"] = "agrees" if record["agrees"] else "disagrees"


def _store(target: dict[str, Any], record: dict[str, Any]) -> None:
    audits = [item for item in target.get("scoring_audits") or [] if isinstance(item, dict)]
    audits.append(record)
    target["scoring_audits"] = audits


def _existing(target: dict[str, Any], key: object) -> dict[str, Any] | None:
    if not isinstance(key, str):
        return None
    matches = [
        item
        for item in target.get("scoring_audits") or []
        if isinstance(item, dict) and item.get("audit_key") == key
    ]
    if not matches:
        return None
    return matches[-1]


def _public(record: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(record))


def _guard(target: dict[str, Any], stages_before: str, publication_before: str) -> None:
    if json.dumps(target.get("specialist_stages"), sort_keys=True) != stages_before:
        raise RuntimeError("The scoring audit changed specialist stages.")
    if json.dumps(target.get("results_publication"), sort_keys=True) != publication_before:
        raise RuntimeError("The scoring audit changed publication.")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def main(argv: list[str] | None = None) -> int:
    """Operator entry point. This does not launch generation."""

    import argparse
    import sys

    from cellertz.application.design_journey import DesignJourneyService, open_design_journey_store

    parser = argparse.ArgumentParser(description="Audit one saved attempt with the existing ipSAE calculator.")
    parser.add_argument("--journey-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--resume-interrupted", action="store_true")
    args = parser.parse_args(argv)
    service = DesignJourneyService(object(), store=open_design_journey_store())
    try:
        status, payload = service.record_scoring_audit(
            args.journey_id,
            args.attempt_id,
            resume_interrupted=args.resume_interrupted,
        )
    finally:
        service.close()
    json.dump(payload, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    if status == 200:
        return 0
    if payload.get("status") in {"verification_failed", "audit_interrupted"}:
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
