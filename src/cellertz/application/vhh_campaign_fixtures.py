"""Operational VHH campaign references.

These fixtures record a configuration that was used. They are not
experimental validation and they are not a default for other targets.
"""

from __future__ import annotations

from cellertz.application.vhh_campaign import (
    AcceptanceMetric,
    AcceptancePolicy,
    ArtifactReference,
    CampaignEvidence,
    ChainMapping,
    ChainRef,
    EpitopeDefinition,
    GeneratorProtocol,
    ResidueReference,
    ScaffoldDefinition,
    ScientificProvenance,
    SequenceSpan,
    StoppingPolicy,
    TargetPreparationStep,
    TargetStructureReference,
    VHHCampaignSpecification,
    blank_evidence,
    mask_spans,
)

# Shared by the recorded mBER configs. Not a contract default.
OPERATIONAL_MASKED_SCAFFOLD = (
    "EVQLVESGGGLVQPGGSLRLSCAAS**********WFRQAPGKEREFV"
    "*****************RFTISRDNAKNTLYLQMNSLRAEDTAVYYC"
    "**************WGQGTLVTVSS"
)
_NOTE = (
    "Operational reference from the recorded VHH campaign configuration. "
    "Not scientifically validated and not experimental proof of binding."
)


def _provenance(*references: str) -> ScientificProvenance:
    return ScientificProvenance(
        "operational_reference",
        _NOTE,
        references,
    )


def _residues(chain_id: str, numbers: tuple[int, ...]) -> tuple[ResidueReference, ...]:
    return tuple(
        ResidueReference(chain_id, number, chain_namespace="prepared") for number in numbers
    )


def _source_partners(*chain_ids: str) -> tuple[ChainRef, ...]:
    return tuple(ChainRef("source", chain_id) for chain_id in chain_ids)


def _scaffold() -> ScaffoldDefinition:
    spans = mask_spans(OPERATIONAL_MASKED_SCAFFOLD)
    names = ("cdr1", "cdr2", "cdr3")
    mutable = tuple(
        SequenceSpan(f"mutable_{index}", start, end)
        for index, (start, end) in enumerate(spans, start=1)
    )
    cdr = tuple(
        SequenceSpan(name, start, end)
        for name, (start, end) in zip(names, spans, strict=True)
    )
    return ScaffoldDefinition(
        design_modality="vhh",
        scaffold_id="operational-vhh-masked-framework",
        masked_sequence=OPERATIONAL_MASKED_SCAFFOLD,
        mutable_regions=mutable,
        cdr_regions=cdr,
        provenance=_provenance(),
        scaffold_version=None,
    )


def _protocol() -> GeneratorProtocol:
    return GeneratorProtocol(
        protocol_id="mber-vhh-operational",
        generator_name="mber",
        design_modality="vhh",
        algorithm_family="alphafold_multimer_backprop",
        scaffold_mode="fixed_masked_vhh",
        evaluation_strategy="independent_refold",
        provenance=_provenance(),
        protocol_version=None,
        generator_version=None,
    )


def _acceptance() -> AcceptancePolicy:
    provenance = _provenance()
    return AcceptancePolicy(
        policy_id="mber-vhh-operational-filters",
        protocol_id="mber-vhh-operational",
        metrics=(
            AcceptanceMetric(
                "i_ptm",
                "gte",
                0.75,
                "generator_acceptance",
                "generator_filter",
                provenance,
            ),
            AcceptanceMetric(
                "plddt",
                "gte",
                0.70,
                "generator_acceptance",
                "generator_filter",
                provenance,
            ),
        ),
        provenance=provenance,
    )


def _stopping() -> StoppingPolicy:
    return StoppingPolicy(
        accepted_design_target=5000,
        max_trajectories=50000,
        provenance=_provenance(),
        stopping_reason=None,
    )


def _steps(
    source: str,
    extracted: str,
    relabeled: str,
    *,
    from_chain: str,
    extra: tuple[TargetPreparationStep, ...] = (),
) -> tuple[TargetPreparationStep, ...]:
    provenance = _provenance(f"PDB:{source.split(':')[1][:4]}")
    ordered = (
        TargetPreparationStep(
            1,
            "remove_solvent",
            (),
            source,
            f"{source}-desolvated",
            provenance,
        ),
        *extra,
        TargetPreparationStep(
            2 + len(extra),
            "extract_chain",
            (("chain_id", from_chain),),
            f"{source}-desolvated",
            extracted,
            provenance,
        ),
        TargetPreparationStep(
            3 + len(extra),
            "relabel_chain",
            (("from_chain", from_chain), ("to_chain", "A")),
            extracted,
            relabeled,
            provenance,
        ),
        TargetPreparationStep(
            4 + len(extra),
            "set_segid",
            (("chain_id", "A"), ("segid", "A")),
            relabeled,
            relabeled,
            provenance,
        ),
    )
    return ordered


def _campaign(
    campaign_id: str,
    target_id: str,
    structure: TargetStructureReference,
    preparation: tuple[TargetPreparationStep, ...],
    epitope: EpitopeDefinition,
    rationale: str,
    evidence: tuple[CampaignEvidence, ...],
) -> VHHCampaignSpecification:
    return VHHCampaignSpecification(
        campaign_id=campaign_id,
        request_id=f"request_{campaign_id}",
        session_id="session_vhh_operational_reference",
        target_id=target_id,
        provenance=_provenance(f"PDB:{structure.source_identifier}"),
        completeness="incomplete",
        target_structure=structure,
        preparation=preparation,
        epitope=epitope,
        scaffold=_scaffold(),
        generator_protocol=_protocol(),
        acceptance_policy=_acceptance(),
        stopping_policy=_stopping(),
        rationale=rationale,
        evidence=evidence,
    )


def cd22_operational_reference() -> VHHCampaignSpecification:
    provenance = _provenance("PDB:7O52")
    structure = TargetStructureReference(
        source_type="pdb",
        source_identifier="7O52",
        target_name="CD22",
        source_target_chain_ids=("U",),
        source_partner_chain_ids=("H", "L"),
        prepared_target_chain_ids=("A",),
        chain_mapping=(ChainMapping("U", "A"),),
        preparation_provenance=provenance,
        preparation_status="recorded",
        original_structure=ArtifactReference("pdb:7O52", "original_structure", provenance),
        prepared_structure=ArtifactReference(
            "pdb:7O52-chain-a", "prepared_structure", provenance
        ),
    )
    epitope = EpitopeDefinition(
        residues=_residues(
            "A",
            (
                624, 625, 626, 627, 628, 631, 632, 633, 635, 636, 637, 638,
                639, 640, 641, 642, 645, 646, 647, 648, 650, 652, 653, 657,
            ),
        ),
        derivation_method="structure_contact",
        provenance=provenance,
        verification_status="operational_reference",
        source_structure_identifier="7O52",
        contact_cutoff_angstrom=4.5,
        partner_chains=_source_partners("H", "L"),
        biological_rationale=(
            "Membrane-proximal CD22 domains 6-7 contacted by the m971 Fab "
            "in PDB 7O52. Residue numbers were measured on original chain U "
            "and kept after that chain was relabeled to A."
        ),
    )
    return _campaign(
        "cd22-operational",
        "CD22",
        structure,
        _steps("pdb:7O52", "pdb:7O52-chain-u", "pdb:7O52-chain-a", from_chain="U"),
        epitope,
        epitope.biological_rationale or "",
        blank_evidence(provenance),
    )


def taci_operational_reference() -> VHHCampaignSpecification:
    provenance = _provenance("PDB:1XU1")
    structure = TargetStructureReference(
        source_type="pdb",
        source_identifier="1XU1",
        target_name="TACI",
        source_target_chain_ids=("R",),
        source_partner_chain_ids=("A", "B", "D"),
        prepared_target_chain_ids=("A",),
        chain_mapping=(ChainMapping("R", "A"),),
        preparation_provenance=ScientificProvenance(
            "operational_reference",
            _NOTE
            + " Source partner chain A in PDB 1XU1 is the ligand. Prepared "
            "target chain A is source chain R after relabel, not that ligand.",
            ("PDB:1XU1",),
        ),
        preparation_status="recorded",
        original_structure=ArtifactReference("pdb:1XU1", "original_structure", provenance),
        prepared_structure=ArtifactReference(
            "pdb:1XU1-chain-a", "prepared_structure", provenance
        ),
    )
    hetero = TargetPreparationStep(
        2,
        "remove_selected_heteroatoms",
        (("resname", "NI"),),
        "pdb:1XU1-desolvated",
        "pdb:1XU1-desolvated",
        provenance,
    )
    epitope = EpitopeDefinition(
        residues=_residues(
            "A",
            (78, 80, 81, 82, 83, 84, 87, 91, 92, 93, 94, 95, 96, 97, 98, 99),
        ),
        derivation_method="structure_contact",
        provenance=provenance,
        verification_status="operational_reference",
        source_structure_identifier="1XU1",
        contact_cutoff_angstrom=4.5,
        partner_chains=_source_partners("A", "B", "D"),
        biological_rationale=(
            "APRIL-binding face of TACI CRD2 in PDB 1XU1. Residue numbers "
            "were measured on original chain R and kept after relabel to A."
        ),
    )
    return _campaign(
        "taci-operational",
        "TACI",
        structure,
        _steps(
            "pdb:1XU1",
            "pdb:1XU1-chain-r",
            "pdb:1XU1-chain-a",
            from_chain="R",
            extra=(hetero,),
        ),
        epitope,
        epitope.biological_rationale or "",
        blank_evidence(provenance),
    )
