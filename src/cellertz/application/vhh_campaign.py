"""Scientific contracts for a VHH design campaign.

A specification is not approval and not execution. Metrics here are
protocol filters. They are not ranking_score, not a binding probability,
and not experimental validation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from cellertz.application.contracts import ApplicationContractError, _text
from cellertz.application.request_draft import ProteinDesignRequest

CONTRACT_VERSION = "vhh-campaign-6c1"
SOURCE_TYPES = frozenset({"pdb"})
PREPARATION_STATUSES = frozenset({"not_prepared", "recorded"})
PREPARATION_OPERATIONS = frozenset(
    {
        "remove_solvent",
        "remove_non_target_chains",
        "extract_chain",
        "relabel_chain",
        "set_segid",
        "remove_selected_heteroatoms",
    }
)
DERIVATION_METHODS = frozenset(
    {"structure_contact", "manually_curated", "inherited_unverified"}
)
MODALITIES = frozenset({"vhh"})
GENERATORS = frozenset({"mber", "bindcraft", "bindcraft2", "rfantibody", "fixture"})
METRIC_DIRECTIONS = frozenset({"gte", "lte", "eq"})
METRIC_ROLES = frozenset(
    {
        "generator_filter",
        "independent_evidence",
        "developability_evidence",
        "off_target_evidence",
    }
)
EVIDENCE_STAGES = (
    "generated",
    "generator_accepted",
    "independently_evaluated",
    "developability_screened",
    "off_target_screened",
    "scientifically_reviewed",
)
PROVENANCE_KINDS = frozenset({"operational_reference", "recorded", "not_recorded"})
CHAIN_NAMESPACES = frozenset({"source", "prepared"})
AMINO_ACIDS = frozenset("ACDEFGHIKLMNPQRSTVWY")
_COMMAND_PREFIXES = ("screen ", "ssh ", "bash ", "sh ", "export ")
MISSING_VHH_FIELDS = (
    "target_structure",
    "target_preparation",
    "epitope",
    "scaffold",
    "generator_protocol",
    "generator_version",
    "acceptance_policy",
    "stopping_policy",
    "rationale",
)
_BLOCKED_METRIC_NAMES = frozenset(
    {"ranking_score", "composite_score", "priority_score", "boltz_score", "boltz"}
)


def _chain(value: object, code: str) -> str:
    text = _text(value, code)
    if len(text) != 1 or not text.isalnum():
        raise ApplicationContractError(code)
    return text.upper()


def _chains(values: tuple[str, ...], code: str) -> tuple[str, ...]:
    found = tuple(_chain(item, code) for item in values)
    if len(found) != len(set(found)):
        raise ApplicationContractError("duplicate_chain")
    return found


def _positive_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ApplicationContractError(code)
    return value


def _finite_number(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApplicationContractError(code)
    number = float(value)
    if not math.isfinite(number):
        raise ApplicationContractError(code)
    return number


def _finite_positive(value: object, code: str) -> float:
    number = _finite_number(value, code)
    if number <= 0:
        raise ApplicationContractError(code)
    return number


def _version(value: str | None) -> str | None:
    if value is None:
        return None
    text = _text(value, "invalid_generator_version")
    if text.casefold() == "latest":
        raise ApplicationContractError("generator_version_must_stay_unknown")
    return text


def _embeds_credential(text: str) -> bool:
    if "://" not in text or "@" not in text:
        return False
    userinfo = text.split("://", 1)[1].split("@", 1)[0]
    return ":" in userinfo


def _artifact_reference(value: str) -> str:
    """Accept an opaque id or URI. The locator is not scientific identity."""
    text = _text(value, "invalid_artifact_reference")
    if _embeds_credential(text) or "\n" in text or "\r" in text:
        raise ApplicationContractError("artifact_reference_embeds_credential_or_runtime")
    if text.lstrip().startswith(_COMMAND_PREFIXES):
        raise ApplicationContractError("artifact_reference_embeds_credential_or_runtime")
    return text


def mask_spans(sequence: str) -> tuple[tuple[int, int], ...]:
    """1-based inclusive spans of each `*` run. Parsing, not a scaffold default."""
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(sequence):
        if sequence[index] != "*":
            index += 1
            continue
        end = index
        while end < len(sequence) and sequence[end] == "*":
            end += 1
        spans.append((index + 1, end))
        index = end
    return tuple(spans)


@dataclass(frozen=True)
class ScientificProvenance:
    kind: str
    note: str
    references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in PROVENANCE_KINDS:
            raise ApplicationContractError("invalid_provenance_kind")
        _text(self.note, "empty_provenance_note")
        for item in self.references:
            _text(item, "empty_reference")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "note": self.note,
            "references": list(self.references),
        }


@dataclass(frozen=True)
class ArtifactReference:
    """Locator plus optional digest. The locator is not scientific identity."""

    reference: str
    role: str
    provenance: ScientificProvenance
    content_digest: str | None = None

    def __post_init__(self) -> None:
        _artifact_reference(self.reference)
        _text(self.role, "invalid_artifact_role")
        if self.content_digest is not None:
            _text(self.content_digest, "invalid_content_digest")

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "role": self.role,
            "content_digest": self.content_digest,
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class ChainRef:
    """A chain letter qualified by the structure namespace it belongs to."""

    namespace: str
    chain_id: str

    def __post_init__(self) -> None:
        if self.namespace not in CHAIN_NAMESPACES:
            raise ApplicationContractError("invalid_chain_namespace")
        object.__setattr__(self, "chain_id", _chain(self.chain_id, "invalid_chain_id"))

    def to_dict(self) -> dict[str, str]:
        return {"namespace": self.namespace, "chain_id": self.chain_id}


@dataclass(frozen=True)
class ChainMapping:
    """How one source chain is written into the prepared target."""

    source_chain_id: str
    prepared_chain_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_chain_id", _chain(self.source_chain_id, "invalid_chain_id")
        )
        object.__setattr__(
            self, "prepared_chain_id", _chain(self.prepared_chain_id, "invalid_chain_id")
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "source_chain_id": self.source_chain_id,
            "prepared_chain_id": self.prepared_chain_id,
        }


@dataclass(frozen=True)
class TargetStructureReference:
    source_type: str
    source_identifier: str
    target_name: str
    source_target_chain_ids: tuple[str, ...]
    source_partner_chain_ids: tuple[str, ...]
    prepared_target_chain_ids: tuple[str, ...]
    chain_mapping: tuple[ChainMapping, ...]
    preparation_provenance: ScientificProvenance
    preparation_status: str
    original_structure: ArtifactReference | None = None
    prepared_structure: ArtifactReference | None = None

    def __post_init__(self) -> None:
        if self.source_type not in SOURCE_TYPES:
            raise ApplicationContractError("invalid_structure_source_type")
        _text(self.source_identifier, "invalid_structure_identifier")
        if "/" in self.source_identifier or "\\" in self.source_identifier:
            raise ApplicationContractError("structure_identity_is_not_a_path")
        _text(self.target_name, "invalid_target_name")
        object.__setattr__(
            self,
            "source_target_chain_ids",
            _chains(self.source_target_chain_ids, "invalid_chain_id"),
        )
        object.__setattr__(
            self,
            "source_partner_chain_ids",
            _chains(self.source_partner_chain_ids, "invalid_chain_id"),
        )
        object.__setattr__(
            self,
            "prepared_target_chain_ids",
            _chains(self.prepared_target_chain_ids, "invalid_chain_id"),
        )
        if not self.source_target_chain_ids or not self.prepared_target_chain_ids:
            raise ApplicationContractError("missing_target_chains")
        if set(self.source_target_chain_ids) & set(self.source_partner_chain_ids):
            raise ApplicationContractError("source_chain_namespaces_overlap")
        mapped_source = tuple(item.source_chain_id for item in self.chain_mapping)
        mapped_prepared = tuple(item.prepared_chain_id for item in self.chain_mapping)
        if (
            mapped_source != self.source_target_chain_ids
            or mapped_prepared != self.prepared_target_chain_ids
        ):
            raise ApplicationContractError("chain_mapping_does_not_match_selected_chains")
        if self.preparation_status not in PREPARATION_STATUSES:
            raise ApplicationContractError("invalid_preparation_status")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_identifier": self.source_identifier,
            "target_name": self.target_name,
            "source_target_chain_ids": list(self.source_target_chain_ids),
            "source_partner_chain_ids": list(self.source_partner_chain_ids),
            "prepared_target_chain_ids": list(self.prepared_target_chain_ids),
            "chain_mapping": [item.to_dict() for item in self.chain_mapping],
            "original_structure": None
            if self.original_structure is None
            else self.original_structure.to_dict(),
            "prepared_structure": None
            if self.prepared_structure is None
            else self.prepared_structure.to_dict(),
            "preparation_provenance": self.preparation_provenance.to_dict(),
            "preparation_status": self.preparation_status,
        }


@dataclass(frozen=True)
class TargetPreparationStep:
    order: int
    operation: str
    parameters: tuple[tuple[str, str], ...]
    source_artifact_reference: str
    output_artifact_reference: str
    provenance: ScientificProvenance

    def __post_init__(self) -> None:
        _positive_int(self.order, "invalid_preparation_order")
        if self.operation not in PREPARATION_OPERATIONS:
            raise ApplicationContractError("invalid_preparation_operation")
        for key, value in self.parameters:
            _text(key, "invalid_preparation_parameter")
            _text(value, "invalid_preparation_parameter")
        _artifact_reference(self.source_artifact_reference)
        _artifact_reference(self.output_artifact_reference)

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "operation": self.operation,
            "parameters": {key: value for key, value in self.parameters},
            "source_artifact_reference": self.source_artifact_reference,
            "output_artifact_reference": self.output_artifact_reference,
            "provenance": self.provenance.to_dict(),
        }


def validate_preparation(steps: tuple[TargetPreparationStep, ...]) -> None:
    orders = [step.order for step in steps]
    if orders != list(range(1, len(orders) + 1)):
        raise ApplicationContractError("preparation_order_must_be_contiguous")


@dataclass(frozen=True)
class ResidueReference:
    chain_id: str
    residue_number: int
    insertion_code: str | None = None
    chain_namespace: str = "prepared"

    def __post_init__(self) -> None:
        if self.chain_namespace not in CHAIN_NAMESPACES:
            raise ApplicationContractError("invalid_chain_namespace")
        object.__setattr__(self, "chain_id", _chain(self.chain_id, "invalid_chain_id"))
        _positive_int(self.residue_number, "invalid_residue_number")
        if self.insertion_code is not None:
            code = _text(self.insertion_code, "invalid_insertion_code")
            if len(code) != 1 or not code.isalnum():
                raise ApplicationContractError("invalid_insertion_code")
            object.__setattr__(self, "insertion_code", code.upper())

    def key(self) -> tuple[str, str, int, str]:
        return (
            self.chain_namespace,
            self.chain_id,
            self.residue_number,
            self.insertion_code or "",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_namespace": self.chain_namespace,
            "chain_id": self.chain_id,
            "residue_number": self.residue_number,
            "insertion_code": self.insertion_code,
        }


@dataclass(frozen=True)
class EpitopeDefinition:
    residues: tuple[ResidueReference, ...]
    derivation_method: str
    provenance: ScientificProvenance
    verification_status: str
    source_structure_identifier: str | None = None
    contact_cutoff_angstrom: float | None = None
    partner_chains: tuple[ChainRef, ...] = ()
    biological_rationale: str | None = None
    measured_span_angstrom: float | None = None

    def __post_init__(self) -> None:
        if not self.residues:
            raise ApplicationContractError("empty_hotspot_set")
        if self.derivation_method not in DERIVATION_METHODS:
            raise ApplicationContractError("invalid_derivation_method")
        seen: set[tuple[str, str, int, str]] = set()
        for residue in self.residues:
            if residue.key() in seen:
                raise ApplicationContractError("duplicate_residue")
            seen.add(residue.key())
        if any(not isinstance(item, ChainRef) for item in self.partner_chains):
            raise ApplicationContractError("invalid_chain_reference")
        if len(self.partner_chains) != len(set(self.partner_chains)):
            raise ApplicationContractError("duplicate_chain")
        if self.contact_cutoff_angstrom is not None:
            _finite_positive(self.contact_cutoff_angstrom, "invalid_contact_cutoff")
        if self.measured_span_angstrom is not None:
            _finite_positive(self.measured_span_angstrom, "invalid_epitope_span")
        if self.biological_rationale is not None:
            _text(self.biological_rationale, "empty_rationale")
        if self.verification_status not in PROVENANCE_KINDS:
            raise ApplicationContractError("invalid_verification_status")
        if self.derivation_method == "structure_contact":
            if self.source_structure_identifier is None:
                raise ApplicationContractError("contact_derivation_requires_structure")
            _text(self.source_structure_identifier, "invalid_structure_identifier")
            if self.contact_cutoff_angstrom is None or not self.partner_chains:
                raise ApplicationContractError("contact_derivation_requires_cutoff_and_partners")
            if any(partner.namespace != "source" for partner in self.partner_chains):
                raise ApplicationContractError("contact_partners_belong_to_source_namespace")
        if (
            self.verification_status == "operational_reference"
            and self.derivation_method == "structure_contact"
            and not self.source_structure_identifier
        ):
            raise ApplicationContractError("contact_derivation_requires_structure")

    def to_dict(self) -> dict[str, Any]:
        return {
            "residues": [residue.to_dict() for residue in self.residues],
            "derivation_method": self.derivation_method,
            "contact_cutoff_angstrom": self.contact_cutoff_angstrom,
            "partner_chains": [partner.to_dict() for partner in self.partner_chains],
            "biological_rationale": self.biological_rationale,
            "source_structure_identifier": self.source_structure_identifier,
            "verification_status": self.verification_status,
            "measured_span_angstrom": self.measured_span_angstrom,
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class SequenceSpan:
    name: str
    start: int
    end: int

    def __post_init__(self) -> None:
        _text(self.name, "invalid_span_name")
        _positive_int(self.start, "invalid_span")
        _positive_int(self.end, "invalid_span")
        if self.end < self.start:
            raise ApplicationContractError("invalid_span")

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "start": self.start, "end": self.end}


@dataclass(frozen=True)
class ScaffoldDefinition:
    design_modality: str
    scaffold_id: str
    masked_sequence: str
    mutable_regions: tuple[SequenceSpan, ...]
    provenance: ScientificProvenance
    scaffold_version: str | None = None
    cdr_regions: tuple[SequenceSpan, ...] = ()

    def __post_init__(self) -> None:
        if self.design_modality not in MODALITIES:
            raise ApplicationContractError("invalid_design_modality")
        _text(self.scaffold_id, "invalid_scaffold_id")
        _version(self.scaffold_version)
        sequence = _text(self.masked_sequence, "empty_masked_sequence")
        if any(char not in AMINO_ACIDS and char != "*" for char in sequence):
            raise ApplicationContractError("invalid_masked_sequence")
        expected = mask_spans(sequence)
        if not expected:
            raise ApplicationContractError("masked_sequence_has_no_mutable_region")
        actual = tuple((span.start, span.end) for span in self.mutable_regions)
        if actual != expected:
            raise ApplicationContractError("mutable_regions_do_not_match_mask")
        mutable = set(expected)
        for cdr in self.cdr_regions:
            if not any(cdr.start >= start and cdr.end <= end for start, end in mutable):
                raise ApplicationContractError("cdr_outside_mutable_region")

    @property
    def fixed_framework(self) -> str:
        return self.masked_sequence.replace("*", "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "design_modality": self.design_modality,
            "scaffold_id": self.scaffold_id,
            "scaffold_version": self.scaffold_version,
            "masked_sequence": self.masked_sequence,
            "fixed_framework": self.fixed_framework,
            "mutable_regions": [span.to_dict() for span in self.mutable_regions],
            "cdr_regions": [span.to_dict() for span in self.cdr_regions],
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class GeneratorProtocol:
    protocol_id: str
    generator_name: str
    design_modality: str
    algorithm_family: str
    scaffold_mode: str
    evaluation_strategy: str
    provenance: ScientificProvenance
    protocol_version: str | None = None
    generator_version: str | None = None

    def __post_init__(self) -> None:
        _text(self.protocol_id, "invalid_protocol_id")
        if self.generator_name not in GENERATORS:
            raise ApplicationContractError("invalid_generator")
        if self.design_modality not in MODALITIES:
            raise ApplicationContractError("invalid_design_modality")
        _text(self.algorithm_family, "invalid_algorithm_family")
        _text(self.scaffold_mode, "invalid_scaffold_mode")
        _text(self.evaluation_strategy, "invalid_evaluation_strategy")
        _version(self.protocol_version)
        _version(self.generator_version)

    @property
    def generator_version_status(self) -> str:
        if self.generator_version is None:
            return "not_recorded"
        return "recorded"

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "protocol_version": self.protocol_version,
            "generator_name": self.generator_name,
            "generator_version": self.generator_version,
            "generator_version_status": self.generator_version_status,
            "design_modality": self.design_modality,
            "algorithm_family": self.algorithm_family,
            "scaffold_mode": self.scaffold_mode,
            "evaluation_strategy": self.evaluation_strategy,
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class AcceptanceMetric:
    metric_name: str
    direction: str
    threshold: float
    evaluation_stage: str
    role: str
    provenance: ScientificProvenance

    def __post_init__(self) -> None:
        name = _text(self.metric_name, "invalid_metric_name")
        if name.casefold() in _BLOCKED_METRIC_NAMES:
            raise ApplicationContractError("metric_is_not_a_ranking_score")
        if name.casefold() == "boltz" or name.casefold().startswith("boltz"):
            raise ApplicationContractError("boltz_is_not_a_campaign_filter")
        if self.direction not in METRIC_DIRECTIONS:
            raise ApplicationContractError("invalid_metric_direction")
        if self.role not in METRIC_ROLES:
            raise ApplicationContractError("invalid_metric_role")
        _finite_number(self.threshold, "invalid_metric_threshold")
        _text(self.evaluation_stage, "invalid_evaluation_stage")

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "direction": self.direction,
            "threshold": self.threshold,
            "evaluation_stage": self.evaluation_stage,
            "role": self.role,
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class AcceptancePolicy:
    policy_id: str
    protocol_id: str
    metrics: tuple[AcceptanceMetric, ...]
    provenance: ScientificProvenance

    def __post_init__(self) -> None:
        _text(self.policy_id, "invalid_policy_id")
        _text(self.protocol_id, "invalid_protocol_id")
        if not self.metrics:
            raise ApplicationContractError("empty_acceptance_policy")
        names = [metric.metric_name for metric in self.metrics]
        if len(names) != len(set(names)):
            raise ApplicationContractError("duplicate_acceptance_metric")

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "protocol_id": self.protocol_id,
            "metrics": [metric.to_dict() for metric in self.metrics],
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class StoppingPolicy:
    accepted_design_target: int
    max_trajectories: int
    provenance: ScientificProvenance
    stopping_reason: str | None = None

    def __post_init__(self) -> None:
        _positive_int(self.accepted_design_target, "invalid_accepted_design_target")
        _positive_int(self.max_trajectories, "invalid_max_trajectories")
        if self.stopping_reason is not None:
            _text(self.stopping_reason, "empty_stopping_reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted_design_target": self.accepted_design_target,
            "max_trajectories": self.max_trajectories,
            "stopping_reason": self.stopping_reason,
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class CampaignEvidence:
    stage: str
    recorded: bool
    provenance: ScientificProvenance

    def __post_init__(self) -> None:
        if self.stage not in EVIDENCE_STAGES:
            raise ApplicationContractError("invalid_evidence_stage")
        if not isinstance(self.recorded, bool):
            raise ApplicationContractError("invalid_evidence_recorded")

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "recorded": self.recorded,
            "provenance": self.provenance.to_dict(),
        }


def blank_evidence(provenance: ScientificProvenance) -> tuple[CampaignEvidence, ...]:
    """Every stage is present and unreached. Nothing is promoted."""
    return tuple(
        CampaignEvidence(stage, False, provenance) for stage in EVIDENCE_STAGES
    )


def _issues(
    target_structure: TargetStructureReference | None,
    preparation: tuple[TargetPreparationStep, ...],
    epitope: EpitopeDefinition | None,
    scaffold: ScaffoldDefinition | None,
    generator_protocol: GeneratorProtocol | None,
    acceptance_policy: AcceptancePolicy | None,
    stopping_policy: StoppingPolicy | None,
    rationale: str | None,
) -> tuple[str, ...]:
    missing: list[str] = []
    if target_structure is None:
        missing.append("missing_target_structure")
    if not preparation:
        missing.append("missing_target_preparation")
    if epitope is None:
        missing.append("missing_epitope")
    if scaffold is None:
        missing.append("missing_scaffold")
    if generator_protocol is None:
        missing.append("missing_generator_protocol")
    if generator_protocol is None or generator_protocol.generator_version is None:
        missing.append("missing_generator_version")
    if acceptance_policy is None:
        missing.append("missing_acceptance_policy")
    if stopping_policy is None:
        missing.append("missing_stopping_policy")
    if rationale is None:
        missing.append("missing_rationale")
    return tuple(missing)


@dataclass(frozen=True)
class VHHCampaignSpecification:
    """Domain specification. Structurally complete is still not authorized."""

    campaign_id: str
    request_id: str
    session_id: str
    target_id: str
    provenance: ScientificProvenance
    completeness: str
    target_structure: TargetStructureReference | None = None
    preparation: tuple[TargetPreparationStep, ...] = ()
    epitope: EpitopeDefinition | None = None
    scaffold: ScaffoldDefinition | None = None
    generator_protocol: GeneratorProtocol | None = None
    acceptance_policy: AcceptancePolicy | None = None
    stopping_policy: StoppingPolicy | None = None
    rationale: str | None = None
    evidence: tuple[CampaignEvidence, ...] = ()
    contract_version: str = CONTRACT_VERSION
    execution_authorized: bool = False
    executable: bool = False

    def __post_init__(self) -> None:
        _text(self.campaign_id, "invalid_campaign_id")
        _text(self.request_id, "invalid_request_id")
        _text(self.session_id, "invalid_session_id")
        _text(self.target_id, "invalid_target_id")
        if self.contract_version != CONTRACT_VERSION:
            raise ApplicationContractError("invalid_contract_version")
        if self.execution_authorized or self.executable:
            raise ApplicationContractError("campaign_is_not_authorized")
        validate_preparation(self.preparation)
        if self.rationale is not None:
            _text(self.rationale, "empty_rationale")
        if (
            self.generator_protocol is not None
            and self.acceptance_policy is not None
            and self.acceptance_policy.protocol_id != self.generator_protocol.protocol_id
        ):
            raise ApplicationContractError("acceptance_policy_protocol_mismatch")
        stages = [item.stage for item in self.evidence]
        if len(stages) != len(set(stages)):
            raise ApplicationContractError("duplicate_evidence_stage")
        issues = _issues(
            self.target_structure,
            self.preparation,
            self.epitope,
            self.scaffold,
            self.generator_protocol,
            self.acceptance_policy,
            self.stopping_policy,
            self.rationale,
        )
        expected = "complete" if not issues else "incomplete"
        if self.completeness != expected:
            raise ApplicationContractError("completeness_mismatch")

    @property
    def issues(self) -> tuple[str, ...]:
        return _issues(
            self.target_structure,
            self.preparation,
            self.epitope,
            self.scaffold,
            self.generator_protocol,
            self.acceptance_policy,
            self.stopping_policy,
            self.rationale,
        )

    @property
    def reproducible_readiness(self) -> str:
        """A recorded generator version is required before reproducible approval.

        ``version_recorded`` is still not approval and not execution.
        """
        protocol = self.generator_protocol
        if protocol is not None and protocol.generator_version is not None:
            return "version_recorded"
        return "not_ready"

    def prepared_target_identity(self) -> dict[str, Any] | None:
        """Components that distinguish one prepared input.

        The PDB id is only the source structure. Artifact locators are omitted
        so a path or URI cannot stand in for a content digest.
        """
        structure = self.target_structure
        if structure is None:
            return None
        prepared = structure.prepared_structure
        epitope = self.epitope
        return {
            "source_structure": {
                "source_type": structure.source_type,
                "source_identifier": structure.source_identifier,
            },
            "source_target_chain_ids": list(structure.source_target_chain_ids),
            "source_partner_chain_ids": list(structure.source_partner_chain_ids),
            "prepared_target_chain_ids": list(structure.prepared_target_chain_ids),
            "chain_mapping": [item.to_dict() for item in structure.chain_mapping],
            "preparation": [
                {
                    "order": step.order,
                    "operation": step.operation,
                    "parameters": dict(step.parameters),
                }
                for step in self.preparation
            ],
            "epitope": None
            if epitope is None
            else {
                "residues": [residue.to_dict() for residue in epitope.residues],
                "derivation_method": epitope.derivation_method,
                "contact_cutoff_angstrom": epitope.contact_cutoff_angstrom,
                "partner_chains": [partner.to_dict() for partner in epitope.partner_chains],
                "source_structure_identifier": epitope.source_structure_identifier,
            },
            "prepared_content_digest": None if prepared is None else prepared.content_digest,
        }

    def to_dict(self) -> dict[str, Any]:
        protocol = self.generator_protocol
        return {
            "campaign_id": self.campaign_id,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "target_id": self.target_id,
            "contract_version": self.contract_version,
            "completeness": self.completeness,
            "issues": list(self.issues),
            "reproducible_readiness": self.reproducible_readiness,
            "execution_authorized": False,
            "executable": False,
            "prepared_target_identity": self.prepared_target_identity(),
            "target_structure": None
            if self.target_structure is None
            else self.target_structure.to_dict(),
            "preparation": [step.to_dict() for step in self.preparation],
            "epitope": None if self.epitope is None else self.epitope.to_dict(),
            "scaffold": None if self.scaffold is None else self.scaffold.to_dict(),
            "generator_protocol": None if protocol is None else protocol.to_dict(),
            "acceptance_policy": None
            if self.acceptance_policy is None
            else self.acceptance_policy.to_dict(),
            "stopping_policy": None
            if self.stopping_policy is None
            else self.stopping_policy.to_dict(),
            "rationale": self.rationale,
            "evidence": [item.to_dict() for item in self.evidence],
            "provenance": self.provenance.to_dict(),
        }


@dataclass(frozen=True)
class VHHCampaignProjection:
    """What a generic ProteinDesignRequest does and does not determine."""

    request_id: str
    session_id: str
    target_id: str
    generator: str
    n_candidates: int
    constraints: tuple[tuple[str, int], ...]
    missing_fields: tuple[str, ...]
    completeness: str = "incomplete"
    specification: VHHCampaignSpecification | None = None
    execution_authorized: bool = False
    executable: bool = False

    def __post_init__(self) -> None:
        if self.completeness != "incomplete" or self.specification is not None:
            raise ApplicationContractError("generic_request_is_not_a_vhh_campaign")
        if self.missing_fields != MISSING_VHH_FIELDS:
            raise ApplicationContractError("vhh_missing_fields_mismatch")
        if self.execution_authorized or self.executable:
            raise ApplicationContractError("campaign_is_not_authorized")

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "target_id": self.target_id,
            "generator": self.generator,
            "n_candidates": self.n_candidates,
            "constraints": {key: value for key, value in self.constraints},
            "missing_fields": list(self.missing_fields),
            "completeness": "incomplete",
            "specification": None,
            "execution_authorized": False,
            "executable": False,
        }


def project_vhh_campaign(request: ProteinDesignRequest) -> VHHCampaignProjection:
    """Copy generic request fields and list every VHH fact the request lacks.

    The target name is not used to choose a PDB, a chain, or a threshold.
    """
    return VHHCampaignProjection(
        request_id=request.request_id,
        session_id=request.session_id,
        target_id=request.target_id,
        generator=request.generator,
        n_candidates=request.n_candidates,
        constraints=request.constraints,
        missing_fields=MISSING_VHH_FIELDS,
    )
