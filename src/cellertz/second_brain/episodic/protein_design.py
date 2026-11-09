"""Project a completed protein-design run into episodic scientific memory.

The projector reads explicit completed-run artifacts, validates them,
and builds an observed ScientificRunMemory. It does not execute
science, mutate run state, persist memory, or promote semantic
knowledge packages.

A completed run remains epistemic status ``observed``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cellertz.second_brain.episodic.models import (
    DOMAIN_PROTEIN_DESIGN,
    EPISTEMIC_STATUS_OBSERVED,
    MEMORY_HOUSE_EPISODIC,
    PATH_PROVENANCE_KEYS,
    SCHEMA_VERSION,
    ArtifactReference,
    CandidateMemory,
    CriticMemory,
    DecisionMemory,
    ExecutionProvenanceMemory,
    IpSAEModelEvidence,
    ScientificRunMemory,
    ScientificRunMemoryProjectionError,
    StructuralEvidenceMemory,
    attach_memory_digest,
    validate_scientific_run_memory,
)

HANDOFF_FILENAME = ".cellertz_execution_handoff.json"
REQUIRED_RUN_ARTIFACTS = (
    "run.json",
    "generation.json",
    "scoring.json",
    "ranking.json",
    "critique.json",
    "prioritization.json",
)
REQUIRED_STAGES = (
    "generation",
    "scoring",
    "ranking",
    "critique",
    "prioritization",
)
CORE_PROVENANCE_KEYS = (
    "execution_id",
    "run_id",
    "manifest_sha256",
)


def project_protein_design_run(
    *,
    run_artifact_dir: Path,
    execution_output_dir: Path,
    recorded_at: str | None = None,
) -> ScientificRunMemory:
    artifact_dir = Path(run_artifact_dir)
    output_dir = Path(execution_output_dir)
    artifacts = _load_required_artifacts(artifact_dir)
    handoff_path = output_dir / HANDOFF_FILENAME
    handoff = _load_handoff(handoff_path)
    run_document = artifacts["run.json"]
    generation = artifacts["generation.json"]
    if not isinstance(run_document, dict):
        raise ScientificRunMemoryProjectionError("run_artifact_malformed")
    if not isinstance(generation, dict):
        raise ScientificRunMemoryProjectionError("generation_artifact_malformed")
    _require_complete_run(run_document, generation)
    run_id = _require_str(run_document.get("run_id"), "run_id")
    handoff_run_id = _require_str(handoff.get("run_id"), "handoff_run_id")
    if run_id != handoff_run_id:
        raise ScientificRunMemoryProjectionError("run_id_mismatch")
    execution_id = _require_str(handoff.get("execution_id"), "execution_id")
    request_hash = _require_str(handoff.get("request_hash"), "request_hash")
    manifest_sha256 = _require_str(
        handoff.get("manifest_sha256"),
        "manifest_sha256",
    )
    scientific_request_sha256 = _require_str(
        handoff.get("scientific_request_sha256"),
        "scientific_request_sha256",
    )
    scientific_request = handoff.get("scientific_request")
    if not isinstance(scientific_request, dict):
        raise ScientificRunMemoryProjectionError("scientific_request_missing")
    generation_request = generation.get("request")
    if not isinstance(generation_request, dict):
        raise ScientificRunMemoryProjectionError("generation_request_missing")
    target_id = _require_matching_str(
        scientific_request.get("target_id"),
        generation_request.get("target_id"),
        run_document.get("target"),
        field="target_id",
    )
    objective = _require_matching_str(
        scientific_request.get("objective"),
        generation_request.get("objective"),
        field="objective",
    )
    generator = _require_matching_str(
        scientific_request.get("generator"),
        generation_request.get("generator"),
        field="generator",
    )
    provenance = _execution_provenance(
        handoff=handoff,
        execution_id=execution_id,
        run_id=run_id,
        manifest_sha256=manifest_sha256,
        request_hash=request_hash,
        scientific_request_sha256=scientific_request_sha256,
    )
    generated = _generated_candidates(generation, target_id=target_id)
    scoring = _index_records(artifacts["scoring.json"], "scoring")
    ranking = _index_records(artifacts["ranking.json"], "ranking")
    critique = _index_records(artifacts["critique.json"], "critique")
    portfolio = _index_records(
        artifacts["prioritization.json"],
        "prioritization",
    )
    _require_candidate_set(scoring, generated, "scoring")
    _require_candidate_set(ranking, generated, "ranking")
    _require_candidate_set(critique, generated, "critique")
    _require_candidate_set(portfolio, generated, "prioritization")
    _cross_check_completed_science(
        generated=generated,
        scoring=scoring,
        ranking=ranking,
        execution_id=execution_id,
    )
    candidates = tuple(
        _candidate_memory(
            generated=item,
            scoring=scoring.get(item.candidate_id),
            ranking=ranking.get(item.candidate_id),
            critique=critique.get(item.candidate_id),
            portfolio=portfolio.get(item.candidate_id),
        )
        for item in generated
    )
    source_artifacts = _source_artifacts(
        artifact_dir=artifact_dir,
        handoff_path=handoff_path,
        candidates=candidates,
        provenance=provenance,
    )
    stamp = recorded_at or datetime.now(UTC).isoformat()
    memory = ScientificRunMemory(
        schema_version=SCHEMA_VERSION,
        memory_id=f"{DOMAIN_PROTEIN_DESIGN}:{run_id}",
        domain=DOMAIN_PROTEIN_DESIGN,
        memory_house=MEMORY_HOUSE_EPISODIC,
        epistemic_status=EPISTEMIC_STATUS_OBSERVED,
        run_id=run_id,
        execution_id=execution_id,
        target_id=target_id,
        objective=objective,
        generator=generator,
        scientific_request_sha256=scientific_request_sha256,
        request_hash=request_hash,
        manifest_sha256=manifest_sha256,
        execution=provenance,
        candidates=candidates,
        source_artifacts=source_artifacts,
        memory_digest="",
        recorded_at=stamp,
    )
    memory = attach_memory_digest(memory)
    validate_scientific_run_memory(memory)
    return memory


def _load_required_artifacts(artifact_dir: Path) -> dict[str, object]:
    loaded: dict[str, object] = {}
    for name in REQUIRED_RUN_ARTIFACTS:
        path = artifact_dir / name
        if not path.is_file():
            raise ScientificRunMemoryProjectionError(f"missing_artifact:{name}")
        loaded[name] = _read_json(path, name)
    return loaded


def _load_handoff(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ScientificRunMemoryProjectionError("missing_artifact:handoff")
    document = _read_json(path, "handoff")
    if not isinstance(document, dict):
        raise ScientificRunMemoryProjectionError("handoff_malformed")
    return document


def _read_json(path: Path, label: str) -> object:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScientificRunMemoryProjectionError(f"{label}_malformed") from exc
    return document


def _require_complete_run(
    run_document: dict[str, Any],
    generation: dict[str, Any],
) -> None:
    if run_document.get("stage") != "complete":
        raise ScientificRunMemoryProjectionError("run_not_complete")
    for name in REQUIRED_STAGES:
        stage = run_document.get(name)
        if not isinstance(stage, dict) or stage.get("status") != "complete":
            raise ScientificRunMemoryProjectionError(f"run_not_complete:{name}")
    if generation.get("status") != "complete":
        raise ScientificRunMemoryProjectionError("generation_not_complete")


def _execution_provenance(
    *,
    handoff: dict[str, Any],
    execution_id: str,
    run_id: str,
    manifest_sha256: str,
    request_hash: str,
    scientific_request_sha256: str,
) -> ExecutionProvenanceMemory:
    provenance = handoff.get("execution_provenance")
    if not isinstance(provenance, dict):
        raise ScientificRunMemoryProjectionError("handoff_provenance_missing")
    for key, expected in (
        ("execution_id", execution_id),
        ("run_id", run_id),
        ("manifest_sha256", manifest_sha256),
    ):
        if key not in provenance:
            raise ScientificRunMemoryProjectionError("handoff_provenance_missing")
        actual = _require_str(provenance.get(key), f"provenance_{key}")
        if actual != expected:
            raise ScientificRunMemoryProjectionError("handoff_provenance_mismatch")
    extra = {
        key: value
        for key, value in provenance.items()
        if key not in CORE_PROVENANCE_KEYS
        and key
        not in {
            "container_digest",
            "tool_version",
            "seed",
            "advanced_profile_sha256",
            *PATH_PROVENANCE_KEYS,
        }
    }
    return ExecutionProvenanceMemory(
        execution_id=execution_id,
        run_id=run_id,
        manifest_sha256=manifest_sha256,
        request_hash=request_hash,
        scientific_request_sha256=scientific_request_sha256,
        container_digest=_optional_str(provenance.get("container_digest")),
        tool_version=_optional_str(provenance.get("tool_version")),
        seed=_optional_int(provenance.get("seed")),
        advanced_profile_sha256=_optional_str(
            provenance.get("advanced_profile_sha256")
        ),
        extra=extra,
    )


def _generated_candidates(
    generation: dict[str, Any],
    *,
    target_id: str,
) -> tuple[CandidateMemory, ...]:
    raw = generation.get("candidates")
    if not isinstance(raw, list):
        raise ScientificRunMemoryProjectionError("generation_candidates_missing")
    seen: set[str] = set()
    generated: list[CandidateMemory] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ScientificRunMemoryProjectionError("generation_candidate_malformed")
        candidate_id = _require_str(item.get("candidate_id"), "candidate_id")
        if candidate_id in seen:
            raise ScientificRunMemoryProjectionError("duplicate_candidate_id")
        seen.add(candidate_id)
        candidate_target = _require_str(item.get("target_id"), "candidate_target_id")
        if candidate_target != target_id:
            raise ScientificRunMemoryProjectionError("candidate_target_mismatch")
        generated.append(
            CandidateMemory(
                candidate_id=candidate_id,
                target_id=candidate_target,
                sequence=_require_str(item.get("sequence"), "sequence"),
                generator=_require_str(item.get("generator"), "generator"),
                rank=None,
                ranking_signal=None,
                ranking_score=None,
                is_probability=None,
                recommended_as_hard_filter=None,
                evidence=None,
                critic=None,
                decision=None,
            )
        )
    return tuple(generated)


def _index_records(payload: object, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, list):
        raise ScientificRunMemoryProjectionError(f"{label}_malformed")
    indexed: dict[str, dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise ScientificRunMemoryProjectionError(f"{label}_malformed")
        candidate_id = _require_str(item.get("candidate_id"), f"{label}_candidate_id")
        if candidate_id in indexed:
            raise ScientificRunMemoryProjectionError("duplicate_candidate_id")
        indexed[candidate_id] = item
    return indexed


def _require_candidate_set(
    indexed: dict[str, dict[str, Any]],
    generated: tuple[CandidateMemory, ...],
    label: str,
) -> None:
    known = {item.candidate_id for item in generated}
    if set(indexed) != known:
        raise ScientificRunMemoryProjectionError(f"candidate_set_mismatch:{label}")


def _cross_check_completed_science(
    *,
    generated: tuple[CandidateMemory, ...],
    scoring: dict[str, dict[str, Any]],
    ranking: dict[str, dict[str, Any]],
    execution_id: str,
) -> None:
    for item in generated:
        _cross_check_candidate(
            scoring=scoring[item.candidate_id],
            ranking=ranking[item.candidate_id],
            execution_id=execution_id,
        )


def _cross_check_candidate(
    *,
    scoring: dict[str, Any],
    ranking: dict[str, Any],
    execution_id: str,
) -> None:
    score = _optional_float(scoring.get("ranking_score"))
    ranked = _optional_float(ranking.get("ranking_score"))
    if score != ranked:
        raise ScientificRunMemoryProjectionError("ranking_score_mismatch")
    evidence = _ipsae_evidence(scoring)
    if evidence is not None:
        evidence_candidate = evidence.get("candidate_id")
        if evidence_candidate != scoring.get("candidate_id"):
            raise ScientificRunMemoryProjectionError(
                "ipsae_evidence_candidate_mismatch"
            )
        evidence_execution = evidence.get("execution_id")
        if evidence_execution is not None and evidence_execution != execution_id:
            raise ScientificRunMemoryProjectionError(
                "ipsae_evidence_execution_mismatch"
            )
        ranking_value = _optional_float(evidence.get("ranking_value"))
        ipsae_mean = _optional_float(evidence.get("ipsae_mean"))
        if ranking_value is not None and ranking_value != score:
            raise ScientificRunMemoryProjectionError("ipsae_score_mismatch")
        if ipsae_mean is not None and ipsae_mean != score:
            raise ScientificRunMemoryProjectionError("ipsae_score_mismatch")
    _require_present_agree(
        _optional_bool(ranking.get("is_probability")),
        _optional_bool(scoring.get("is_probability")),
        None if evidence is None else _optional_bool(evidence.get("is_probability")),
        code="is_probability_mismatch",
    )
    _require_present_agree(
        _optional_bool(ranking.get("recommended_as_hard_filter")),
        _optional_bool(scoring.get("recommended_as_hard_filter")),
        None
        if evidence is None
        else _optional_bool(evidence.get("recommended_as_hard_filter")),
        code="recommended_as_hard_filter_mismatch",
    )


def _ipsae_evidence(scoring: dict[str, Any]) -> dict[str, Any] | None:
    provenance = scoring.get("provenance")
    if provenance is None:
        return None
    if not isinstance(provenance, dict):
        raise ScientificRunMemoryProjectionError("scoring_provenance_malformed")
    evidence = provenance.get("ipsae_evidence")
    if evidence is None:
        return None
    if not isinstance(evidence, dict):
        raise ScientificRunMemoryProjectionError("ipsae_evidence_malformed")
    return evidence


def _require_present_agree(*values: object, code: str) -> None:
    present = [value for value in values if value is not None]
    if present and any(value != present[0] for value in present):
        raise ScientificRunMemoryProjectionError(code)


def _candidate_memory(
    *,
    generated: CandidateMemory,
    scoring: dict[str, Any] | None,
    ranking: dict[str, Any] | None,
    critique: dict[str, Any] | None,
    portfolio: dict[str, Any] | None,
) -> CandidateMemory:
    return CandidateMemory(
        candidate_id=generated.candidate_id,
        target_id=generated.target_id,
        sequence=generated.sequence,
        generator=generated.generator,
        rank=_optional_int(None if ranking is None else ranking.get("rank")),
        ranking_signal=_optional_str(
            None if ranking is None else ranking.get("ranking_signal")
        ),
        ranking_score=_optional_float(
            None if ranking is None else ranking.get("ranking_score")
        ),
        is_probability=_optional_bool(
            None if ranking is None else ranking.get("is_probability")
        ),
        recommended_as_hard_filter=_optional_bool(
            None
            if ranking is None
            else ranking.get("recommended_as_hard_filter")
        ),
        evidence=_structural_evidence(scoring),
        critic=_critic_memory(critique, portfolio),
        decision=_decision_memory(portfolio),
    )


def _structural_evidence(
    scoring: dict[str, Any] | None,
) -> StructuralEvidenceMemory | None:
    if scoring is None:
        return None
    provenance = scoring.get("provenance")
    if not isinstance(provenance, dict):
        return None
    evidence = provenance.get("ipsae_evidence")
    if not isinstance(evidence, dict):
        return None
    models_raw = evidence.get("models")
    if models_raw is None:
        models: tuple[IpSAEModelEvidence, ...] = ()
    elif not isinstance(models_raw, list):
        raise ScientificRunMemoryProjectionError("ipsae_models_malformed")
    else:
        models = tuple(_model_evidence(item) for item in models_raw)
    parameters = evidence.get("protocol_parameters")
    return StructuralEvidenceMemory(
        protocol_id=_optional_str(evidence.get("protocol_id")),
        protocol_parameters=(
            None if not isinstance(parameters, dict) else dict(parameters)
        ),
        model_aggregation=_optional_str(evidence.get("model_aggregation")),
        n_models=_optional_int(evidence.get("n_models")),
        ipsae_mean=_optional_float(evidence.get("ipsae_mean")),
        ipsae_min=_optional_float(evidence.get("ipsae_min")),
        ipsae_max=_optional_float(evidence.get("ipsae_max")),
        is_probability=_optional_bool(evidence.get("is_probability")),
        recommended_as_hard_filter=_optional_bool(
            evidence.get("recommended_as_hard_filter")
        ),
        models=models,
    )


def _model_evidence(item: object) -> IpSAEModelEvidence:
    if not isinstance(item, dict):
        raise ScientificRunMemoryProjectionError("ipsae_models_malformed")
    model_number = item.get("model_number")
    if type(model_number) is not int:
        raise ScientificRunMemoryProjectionError("ipsae_models_malformed")
    return IpSAEModelEvidence(
        model_number=model_number,
        ipsae=_optional_float(item.get("ipsae")),
        ipsae_ab=_optional_float(item.get("ipsae_ab")),
        ipsae_ba=_optional_float(item.get("ipsae_ba")),
        pdb_sha256=_optional_str(item.get("pdb_sha256")),
        pae_sha256=_optional_str(item.get("pae_sha256")),
    )


def _critic_memory(
    critique: dict[str, Any] | None,
    portfolio: dict[str, Any] | None,
) -> CriticMemory | None:
    assessment = None if critique is None else critique.get("critic_assessment")
    if assessment is None and portfolio is None:
        return None
    if assessment is not None and not isinstance(assessment, dict):
        raise ScientificRunMemoryProjectionError("critique_malformed")
    agreement = (
        None
        if not isinstance(assessment, dict)
        else assessment.get("agreement")
    )
    conflict_raw = (
        None
        if not isinstance(assessment, dict)
        else assessment.get("structural_conflict")
    )
    if conflict_raw == "unknown":
        conflict = None
    else:
        conflict = _optional_bool(conflict_raw)
    missing = (
        None
        if not isinstance(assessment, dict)
        else _optional_bool(assessment.get("missing_evidence"))
    )
    review_flag = (
        None
        if portfolio is None
        else _optional_bool(portfolio.get("review_flag"))
    )
    if (
        agreement is None
        and conflict is None
        and missing is None
        and review_flag is None
    ):
        return None
    return CriticMemory(
        critic_agreement=_optional_str(agreement),
        structural_conflict=conflict,
        missing_evidence=missing,
        review_flag=review_flag,
    )


def _decision_memory(portfolio: dict[str, Any] | None) -> DecisionMemory | None:
    if portfolio is None:
        return None
    return DecisionMemory(
        selected=_optional_bool(portfolio.get("selected")),
        selection_bucket=_optional_str(portfolio.get("selection_bucket")),
        selection_order=_optional_int(portfolio.get("selection_order")),
        selection_reason=_optional_str(portfolio.get("selection_reason")),
    )


def _source_artifacts(
    *,
    artifact_dir: Path,
    handoff_path: Path,
    candidates: tuple[CandidateMemory, ...],
    provenance: ExecutionProvenanceMemory,
) -> tuple[ArtifactReference, ...]:
    artifacts = [
        ArtifactReference(
            artifact_type=name.replace(".json", ""),
            sha256=_file_sha256(artifact_dir / name),
            uri=str(artifact_dir / name),
        )
        for name in REQUIRED_RUN_ARTIFACTS
    ]
    artifacts.append(
        ArtifactReference(
            artifact_type="execution_handoff",
            sha256=_file_sha256(handoff_path),
            uri=str(handoff_path),
        )
    )
    if provenance.container_digest is not None:
        artifacts.append(
            ArtifactReference(
                artifact_type="container",
                sha256=provenance.container_digest,
            )
        )
    for candidate in candidates:
        if candidate.evidence is None:
            continue
        for model in candidate.evidence.models:
            if model.pdb_sha256 is not None:
                artifacts.append(
                    ArtifactReference(
                        artifact_type="pdb",
                        sha256=model.pdb_sha256,
                        candidate_id=candidate.candidate_id,
                        model_number=model.model_number,
                    )
                )
            if model.pae_sha256 is not None:
                artifacts.append(
                    ArtifactReference(
                        artifact_type="pae",
                        sha256=model.pae_sha256,
                        candidate_id=candidate.candidate_id,
                        model_number=model.model_number,
                    )
                )
    artifacts.sort(
        key=lambda item: (
            item.artifact_type,
            item.candidate_id or "",
            item.model_number if item.model_number is not None else -1,
            item.sha256 or "",
        )
    )
    return tuple(artifacts)


def _require_matching_str(*values: object, field: str) -> str:
    strings = [_require_str(value, field) for value in values if value is not None]
    if not strings:
        raise ScientificRunMemoryProjectionError(f"{field}_missing")
    if len(set(strings)) != 1:
        raise ScientificRunMemoryProjectionError(f"{field}_mismatch")
    return strings[0]


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ScientificRunMemoryProjectionError(f"{field}_missing")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ScientificRunMemoryProjectionError("optional_str_invalid")
    return value


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise ScientificRunMemoryProjectionError("optional_bool_invalid")
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ScientificRunMemoryProjectionError("optional_int_invalid")
    return value


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if type(value) is bool or type(value) not in {int, float}:
        raise ScientificRunMemoryProjectionError("optional_float_invalid")
    return float(value)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
