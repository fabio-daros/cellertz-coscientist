"""Episodic scientific run memory models.

ScientificRunMemory is a durable episodic record of observed
scientific execution and results from a completed run. It is not
operational run state, not idempotency, not a KnowledgePackage, and
not a RAG index.

RUN COMPLETE != SCIENTIFIC CLAIM VALIDATED.
EPISODIC MEMORY != VALIDATED SEMANTIC MEMORY.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any

SCHEMA_VERSION = "scientific_run_memory_v1"
DOMAIN_PROTEIN_DESIGN = "protein_design"
MEMORY_HOUSE_EPISODIC = "episodic"
EPISTEMIC_STATUS_OBSERVED = "observed"
FORBIDDEN_EPISTEMIC_STATUSES = frozenset(
    {
        "validated",
        "approved",
        "confirmed",
        "experimentally validated",
        "experimentally_validated",
    }
)

PATH_PROVENANCE_KEYS = frozenset(
    {
        "container_image",
        "execution_container_image",
    }
)


class ScientificRunMemoryError(Exception):
    """Fail-closed episodic scientific run memory error."""


class ScientificRunMemoryConflict(ScientificRunMemoryError):
    """Same run_id already stores a different scientific digest."""


class ScientificRunMemoryProjectionError(ScientificRunMemoryError):
    """Completed-run artifacts are incomplete or inconsistent."""


@dataclass(frozen=True)
class IpSAEModelEvidence:
    model_number: int
    ipsae: float | None
    ipsae_ab: float | None
    ipsae_ba: float | None
    pdb_sha256: str | None
    pae_sha256: str | None

    def to_document(self) -> dict[str, Any]:
        return {
            "model_number": self.model_number,
            "ipsae": self.ipsae,
            "ipsae_ab": self.ipsae_ab,
            "ipsae_ba": self.ipsae_ba,
            "pdb_sha256": self.pdb_sha256,
            "pae_sha256": self.pae_sha256,
        }


@dataclass(frozen=True)
class StructuralEvidenceMemory:
    protocol_id: str | None = None
    protocol_parameters: dict[str, Any] | None = None
    model_aggregation: str | None = None
    n_models: int | None = None
    ipsae_mean: float | None = None
    ipsae_min: float | None = None
    ipsae_max: float | None = None
    is_probability: bool | None = None
    recommended_as_hard_filter: bool | None = None
    models: tuple[IpSAEModelEvidence, ...] = ()

    def to_document(self) -> dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "protocol_parameters": (
                None
                if self.protocol_parameters is None
                else dict(self.protocol_parameters)
            ),
            "model_aggregation": self.model_aggregation,
            "n_models": self.n_models,
            "ipsae_mean": self.ipsae_mean,
            "ipsae_min": self.ipsae_min,
            "ipsae_max": self.ipsae_max,
            "is_probability": self.is_probability,
            "recommended_as_hard_filter": self.recommended_as_hard_filter,
            "models": [model.to_document() for model in self.models],
        }


@dataclass(frozen=True)
class CriticMemory:
    critic_agreement: str | None = None
    structural_conflict: bool | None = None
    missing_evidence: bool | None = None
    review_flag: bool | None = None

    def to_document(self) -> dict[str, Any]:
        return {
            "critic_agreement": self.critic_agreement,
            "structural_conflict": self.structural_conflict,
            "missing_evidence": self.missing_evidence,
            "review_flag": self.review_flag,
        }


@dataclass(frozen=True)
class DecisionMemory:
    selected: bool | None = None
    selection_bucket: str | None = None
    selection_order: int | None = None
    selection_reason: str | None = None

    def to_document(self) -> dict[str, Any]:
        return {
            "selected": self.selected,
            "selection_bucket": self.selection_bucket,
            "selection_order": self.selection_order,
            "selection_reason": self.selection_reason,
        }


@dataclass(frozen=True)
class ArtifactReference:
    artifact_type: str
    sha256: str | None
    candidate_id: str | None = None
    model_number: int | None = None
    uri: str | None = None

    def to_document(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "sha256": self.sha256,
            "candidate_id": self.candidate_id,
            "model_number": self.model_number,
            "uri": self.uri,
        }

    def to_canonical_document(self) -> dict[str, Any]:
        document = self.to_document()
        document.pop("uri", None)
        return document


@dataclass(frozen=True)
class ExecutionProvenanceMemory:
    execution_id: str
    run_id: str
    manifest_sha256: str
    request_hash: str
    scientific_request_sha256: str
    container_digest: str | None = None
    tool_version: str | None = None
    seed: int | None = None
    advanced_profile_sha256: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_document(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "run_id": self.run_id,
            "manifest_sha256": self.manifest_sha256,
            "request_hash": self.request_hash,
            "scientific_request_sha256": self.scientific_request_sha256,
            "container_digest": self.container_digest,
            "tool_version": self.tool_version,
            "seed": self.seed,
            "advanced_profile_sha256": self.advanced_profile_sha256,
            "extra": dict(self.extra),
        }


@dataclass(frozen=True)
class CandidateMemory:
    candidate_id: str
    target_id: str
    sequence: str
    generator: str
    rank: int | None
    ranking_signal: str | None
    ranking_score: float | None
    is_probability: bool | None
    recommended_as_hard_filter: bool | None
    evidence: StructuralEvidenceMemory | None
    critic: CriticMemory | None
    decision: DecisionMemory | None

    def to_document(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "target_id": self.target_id,
            "sequence": self.sequence,
            "generator": self.generator,
            "rank": self.rank,
            "ranking_signal": self.ranking_signal,
            "ranking_score": self.ranking_score,
            "is_probability": self.is_probability,
            "recommended_as_hard_filter": self.recommended_as_hard_filter,
            "evidence": (
                None if self.evidence is None else self.evidence.to_document()
            ),
            "critic": None if self.critic is None else self.critic.to_document(),
            "decision": (
                None if self.decision is None else self.decision.to_document()
            ),
        }


@dataclass(frozen=True)
class CandidateMemoryHit:
    run_id: str
    candidate: CandidateMemory

    def to_document(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "candidate": self.candidate.to_document(),
        }


@dataclass(frozen=True)
class ScientificRunMemory:
    schema_version: str
    memory_id: str
    domain: str
    memory_house: str
    epistemic_status: str
    run_id: str
    execution_id: str
    target_id: str
    objective: str
    generator: str
    scientific_request_sha256: str
    request_hash: str
    manifest_sha256: str
    execution: ExecutionProvenanceMemory
    candidates: tuple[CandidateMemory, ...]
    source_artifacts: tuple[ArtifactReference, ...]
    memory_digest: str
    recorded_at: str

    def __post_init__(self) -> None:
        if self.memory_house != MEMORY_HOUSE_EPISODIC:
            raise ScientificRunMemoryError("memory_house_must_be_episodic")
        if (
            self.epistemic_status in FORBIDDEN_EPISTEMIC_STATUSES
            or self.epistemic_status != EPISTEMIC_STATUS_OBSERVED
        ):
            raise ScientificRunMemoryError("epistemic_status_must_be_observed")

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "memory_id": self.memory_id,
            "domain": self.domain,
            "memory_house": self.memory_house,
            "epistemic_status": self.epistemic_status,
            "run_id": self.run_id,
            "execution_id": self.execution_id,
            "target_id": self.target_id,
            "objective": self.objective,
            "generator": self.generator,
            "scientific_request_sha256": self.scientific_request_sha256,
            "request_hash": self.request_hash,
            "manifest_sha256": self.manifest_sha256,
            "execution": self.execution.to_document(),
            "candidates": [
                candidate.to_document() for candidate in self.candidates
            ],
            "source_artifacts": [
                artifact.to_document() for artifact in self.source_artifacts
            ],
            "memory_digest": self.memory_digest,
            "recorded_at": self.recorded_at,
        }

    def to_canonical_document(self) -> dict[str, Any]:
        document = self.to_document()
        document.pop("recorded_at", None)
        document.pop("memory_digest", None)
        document["source_artifacts"] = [
            artifact.to_canonical_document()
            for artifact in self.source_artifacts
        ]
        return document


def canonical_memory_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_memory_digest(memory: ScientificRunMemory) -> None:
    expected = canonical_memory_digest(memory.to_canonical_document())
    if memory.memory_digest != expected:
        raise ScientificRunMemoryError("scientific_memory_digest_mismatch")


def validate_scientific_run_memory(memory: ScientificRunMemory) -> None:
    if memory.schema_version != SCHEMA_VERSION:
        raise ScientificRunMemoryError("schema_version_mismatch")
    if memory.domain != DOMAIN_PROTEIN_DESIGN:
        raise ScientificRunMemoryError("domain_mismatch")
    expected_id = f"{DOMAIN_PROTEIN_DESIGN}:{memory.run_id}"
    if memory.memory_id != expected_id:
        raise ScientificRunMemoryError("memory_id_mismatch")
    execution = memory.execution
    for name in (
        "execution_id",
        "run_id",
        "manifest_sha256",
        "request_hash",
        "scientific_request_sha256",
    ):
        if getattr(memory, name) != getattr(execution, name):
            raise ScientificRunMemoryError(f"{name}_mismatch")
    validate_memory_digest(memory)


def attach_memory_digest(memory: ScientificRunMemory) -> ScientificRunMemory:
    digest = canonical_memory_digest(memory.to_canonical_document())
    return replace(memory, memory_digest=digest)


def scientific_run_memory_from_document(
    document: dict[str, Any],
) -> ScientificRunMemory:
    if not isinstance(document, dict):
        raise ScientificRunMemoryError("scientific_memory_malformed")
    execution_doc = document.get("execution")
    if not isinstance(execution_doc, dict):
        raise ScientificRunMemoryError("execution_malformed")
    candidates_doc = document.get("candidates")
    if not isinstance(candidates_doc, list):
        raise ScientificRunMemoryError("candidates_malformed")
    artifacts_doc = document.get("source_artifacts")
    if not isinstance(artifacts_doc, list):
        raise ScientificRunMemoryError("source_artifacts_malformed")
    extra = execution_doc.get("extra")
    if extra is None:
        extra_doc: dict[str, Any] = {}
    elif isinstance(extra, dict):
        extra_doc = dict(extra)
    else:
        raise ScientificRunMemoryError("execution_extra_malformed")
    memory = ScientificRunMemory(
        schema_version=_require_str(document.get("schema_version"), "schema_version"),
        memory_id=_require_str(document.get("memory_id"), "memory_id"),
        domain=_require_str(document.get("domain"), "domain"),
        memory_house=_require_str(document.get("memory_house"), "memory_house"),
        epistemic_status=_require_str(
            document.get("epistemic_status"),
            "epistemic_status",
        ),
        run_id=_require_str(document.get("run_id"), "run_id"),
        execution_id=_require_str(document.get("execution_id"), "execution_id"),
        target_id=_require_str(document.get("target_id"), "target_id"),
        objective=_require_str(document.get("objective"), "objective"),
        generator=_require_str(document.get("generator"), "generator"),
        scientific_request_sha256=_require_str(
            document.get("scientific_request_sha256"),
            "scientific_request_sha256",
        ),
        request_hash=_require_str(document.get("request_hash"), "request_hash"),
        manifest_sha256=_require_str(
            document.get("manifest_sha256"),
            "manifest_sha256",
        ),
        execution=ExecutionProvenanceMemory(
            execution_id=_require_str(
                execution_doc.get("execution_id"),
                "execution.execution_id",
            ),
            run_id=_require_str(execution_doc.get("run_id"), "execution.run_id"),
            manifest_sha256=_require_str(
                execution_doc.get("manifest_sha256"),
                "execution.manifest_sha256",
            ),
            request_hash=_require_str(
                execution_doc.get("request_hash"),
                "execution.request_hash",
            ),
            scientific_request_sha256=_require_str(
                execution_doc.get("scientific_request_sha256"),
                "execution.scientific_request_sha256",
            ),
            container_digest=_optional_str(execution_doc.get("container_digest")),
            tool_version=_optional_str(execution_doc.get("tool_version")),
            seed=_optional_int(execution_doc.get("seed")),
            advanced_profile_sha256=_optional_str(
                execution_doc.get("advanced_profile_sha256")
            ),
            extra=extra_doc,
        ),
        candidates=tuple(_candidate_from_document(item) for item in candidates_doc),
        source_artifacts=tuple(
            _artifact_from_document(item) for item in artifacts_doc
        ),
        memory_digest=_require_str(document.get("memory_digest"), "memory_digest"),
        recorded_at=_require_str(document.get("recorded_at"), "recorded_at"),
    )
    validate_scientific_run_memory(memory)
    return memory


def _candidate_from_document(document: object) -> CandidateMemory:
    if not isinstance(document, dict):
        raise ScientificRunMemoryError("candidate_malformed")
    evidence_doc = document.get("evidence")
    critic_doc = document.get("critic")
    decision_doc = document.get("decision")
    if evidence_doc is not None and not isinstance(evidence_doc, dict):
        raise ScientificRunMemoryError("evidence_malformed")
    if critic_doc is not None and not isinstance(critic_doc, dict):
        raise ScientificRunMemoryError("critic_malformed")
    if decision_doc is not None and not isinstance(decision_doc, dict):
        raise ScientificRunMemoryError("decision_malformed")
    return CandidateMemory(
        candidate_id=_require_str(document.get("candidate_id"), "candidate_id"),
        target_id=_require_str(document.get("target_id"), "target_id"),
        sequence=_require_str(document.get("sequence"), "sequence"),
        generator=_require_str(document.get("generator"), "generator"),
        rank=_optional_int(document.get("rank")),
        ranking_signal=_optional_str(document.get("ranking_signal")),
        ranking_score=_optional_float(document.get("ranking_score")),
        is_probability=_optional_bool(document.get("is_probability")),
        recommended_as_hard_filter=_optional_bool(
            document.get("recommended_as_hard_filter")
        ),
        evidence=(
            None
            if evidence_doc is None
            else _evidence_from_document(evidence_doc)
        ),
        critic=(
            None
            if critic_doc is None
            else CriticMemory(
                critic_agreement=_optional_str(critic_doc.get("critic_agreement")),
                structural_conflict=_optional_bool(
                    critic_doc.get("structural_conflict")
                ),
                missing_evidence=_optional_bool(critic_doc.get("missing_evidence")),
                review_flag=_optional_bool(critic_doc.get("review_flag")),
            )
        ),
        decision=(
            None
            if decision_doc is None
            else DecisionMemory(
                selected=_optional_bool(decision_doc.get("selected")),
                selection_bucket=_optional_str(
                    decision_doc.get("selection_bucket")
                ),
                selection_order=_optional_int(decision_doc.get("selection_order")),
                selection_reason=_optional_str(
                    decision_doc.get("selection_reason")
                ),
            )
        ),
    )


def _evidence_from_document(
    document: dict[str, Any],
) -> StructuralEvidenceMemory:
    models_raw = document.get("models")
    if models_raw is None:
        models: tuple[IpSAEModelEvidence, ...] = ()
    elif not isinstance(models_raw, list):
        raise ScientificRunMemoryError("ipsae_models_malformed")
    else:
        models = tuple(_model_from_document(item) for item in models_raw)
    parameters = document.get("protocol_parameters")
    if parameters is not None and not isinstance(parameters, dict):
        raise ScientificRunMemoryError("protocol_parameters_malformed")
    return StructuralEvidenceMemory(
        protocol_id=_optional_str(document.get("protocol_id")),
        protocol_parameters=None if parameters is None else dict(parameters),
        model_aggregation=_optional_str(document.get("model_aggregation")),
        n_models=_optional_int(document.get("n_models")),
        ipsae_mean=_optional_float(document.get("ipsae_mean")),
        ipsae_min=_optional_float(document.get("ipsae_min")),
        ipsae_max=_optional_float(document.get("ipsae_max")),
        is_probability=_optional_bool(document.get("is_probability")),
        recommended_as_hard_filter=_optional_bool(
            document.get("recommended_as_hard_filter")
        ),
        models=models,
    )


def _model_from_document(document: object) -> IpSAEModelEvidence:
    if not isinstance(document, dict):
        raise ScientificRunMemoryError("ipsae_models_malformed")
    model_number = document.get("model_number")
    if type(model_number) is not int:
        raise ScientificRunMemoryError("ipsae_models_malformed")
    return IpSAEModelEvidence(
        model_number=model_number,
        ipsae=_optional_float(document.get("ipsae")),
        ipsae_ab=_optional_float(document.get("ipsae_ab")),
        ipsae_ba=_optional_float(document.get("ipsae_ba")),
        pdb_sha256=_optional_str(document.get("pdb_sha256")),
        pae_sha256=_optional_str(document.get("pae_sha256")),
    )


def _artifact_from_document(document: object) -> ArtifactReference:
    if not isinstance(document, dict):
        raise ScientificRunMemoryError("source_artifacts_malformed")
    return ArtifactReference(
        artifact_type=_require_str(document.get("artifact_type"), "artifact_type"),
        sha256=_optional_str(document.get("sha256")),
        candidate_id=_optional_str(document.get("candidate_id")),
        model_number=_optional_int(document.get("model_number")),
        uri=_optional_str(document.get("uri")),
    )


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ScientificRunMemoryError(f"{field}_malformed")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ScientificRunMemoryError("optional_str_malformed")
    return value


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise ScientificRunMemoryError("optional_bool_malformed")
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ScientificRunMemoryError("optional_int_malformed")
    return value


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if type(value) is bool or type(value) not in {int, float}:
        raise ScientificRunMemoryError("optional_float_malformed")
    return float(value)
