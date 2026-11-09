"""Phase 6C.1 VHH campaign contracts. No execution and no memory writes."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.request_draft import ProteinDesignRequest
from cellertz.application.vhh_campaign import (
    AcceptanceMetric,
    ArtifactReference,
    CampaignEvidence,
    ChainMapping,
    ChainRef,
    EpitopeDefinition,
    ResidueReference,
    ScientificProvenance,
    VHHCampaignSpecification,
    project_vhh_campaign,
)
from cellertz.application.vhh_campaign_fixtures import (
    cd22_operational_reference,
    taci_operational_reference,
)

_PROVENANCE = ScientificProvenance("operational_reference", "Recorded for a test.")


def _request(target_id: str = "CD22") -> ProteinDesignRequest:
    return ProteinDesignRequest(
        request_id="request_cd22",
        session_id="session_vhh",
        revision=1,
        target_id=target_id,
        objective="binder_design",
        generator="mber",
        n_candidates=10,
        constraints=(),
    )


def test_cd22_and_taci_operational_references() -> None:
    cd22 = cd22_operational_reference()
    taci = taci_operational_reference()
    assert cd22.completeness == "incomplete"
    assert cd22.issues == ("missing_generator_version",)
    assert cd22.reproducible_readiness == "not_ready"
    assert cd22.provenance.kind == "operational_reference"
    assert cd22.target_structure is not None
    assert cd22.target_structure.source_identifier == "7O52"
    assert cd22.target_structure.source_target_chain_ids == ("U",)
    assert cd22.target_structure.prepared_target_chain_ids == ("A",)
    assert cd22.target_structure.source_partner_chain_ids == ("H", "L")
    assert cd22.epitope is not None
    assert cd22.epitope.contact_cutoff_angstrom == 4.5
    assert cd22.epitope.derivation_method == "structure_contact"
    assert cd22.epitope.residues[0].chain_namespace == "prepared"
    assert cd22.epitope.residues[0].chain_id == "A"
    assert [residue.residue_number for residue in cd22.epitope.residues] == [
        624, 625, 626, 627, 628, 631, 632, 633, 635, 636, 637, 638,
        639, 640, 641, 642, 645, 646, 647, 648, 650, 652, 653, 657,
    ]
    assert cd22.stopping_policy is not None
    assert cd22.stopping_policy.accepted_design_target == 5000
    assert cd22.stopping_policy.max_trajectories == 50000
    assert taci.target_structure is not None
    assert taci.target_structure.source_identifier == "1XU1"
    assert taci.target_structure.source_target_chain_ids == ("R",)
    assert taci.epitope is not None
    assert taci.epitope.partner_chains == _source("A", "B", "D")
    assert taci.completeness == "incomplete"
    assert taci.issues == ("missing_generator_version",)
    assert [step.operation for step in taci.preparation] == [
        "remove_solvent",
        "remove_selected_heteroatoms",
        "extract_chain",
        "relabel_chain",
        "set_segid",
    ]
    assert [residue.residue_number for residue in taci.epitope.residues] == [
        78, 80, 81, 82, 83, 84, 87, 91, 92, 93, 94, 95, 96, 97, 98, 99,
    ]
    assert taci.provenance.kind == "operational_reference"
    assert "scientifically_validated" not in json.dumps(cd22.to_dict())
    assert "scientifically_validated" not in json.dumps(taci.to_dict())


def _source(*chain_ids: str) -> tuple[ChainRef, ...]:
    return tuple(ChainRef("source", chain_id) for chain_id in chain_ids)


def test_residues_are_structured_and_duplicates_are_rejected() -> None:
    residue = ResidueReference("u", 624)
    assert residue.to_dict()["chain_id"] == "U"
    assert residue.chain_namespace == "prepared"
    with pytest.raises(ApplicationContractError) as caught:
        EpitopeDefinition(
            (ResidueReference("A", 10), ResidueReference("A", 10)),
            "manually_curated",
            _PROVENANCE,
            "operational_reference",
        )
    assert caught.value.code == "duplicate_residue"
    with pytest.raises(ApplicationContractError) as empty:
        EpitopeDefinition((), "manually_curated", _PROVENANCE, "operational_reference")
    assert empty.value.code == "empty_hotspot_set"
    with pytest.raises(ApplicationContractError):
        ResidueReference("AB", 1)
    with pytest.raises(ApplicationContractError):
        ResidueReference("A", 0)


def test_invalid_cutoff_and_contact_without_structure_are_rejected() -> None:
    residue = (ResidueReference("A", 10),)
    for cutoff in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ApplicationContractError) as caught:
            EpitopeDefinition(
                residue,
                "structure_contact",
                _PROVENANCE,
                "operational_reference",
                source_structure_identifier="7O52",
                contact_cutoff_angstrom=cutoff,
                partner_chains=_source("H"),
            )
        assert caught.value.code == "invalid_contact_cutoff"
        assert not math.isfinite(cutoff) or cutoff <= 0
    with pytest.raises(ApplicationContractError) as missing:
        EpitopeDefinition(
            residue,
            "structure_contact",
            _PROVENANCE,
            "operational_reference",
            contact_cutoff_angstrom=4.5,
            partner_chains=_source("H"),
        )
    assert missing.value.code == "contact_derivation_requires_structure"


def test_preparation_is_provenance_and_paths_are_not_identity() -> None:
    campaign = cd22_operational_reference()
    operations = [step.operation for step in campaign.preparation]
    assert operations == ["remove_solvent", "extract_chain", "relabel_chain", "set_segid"]
    assert campaign.preparation[2].parameters == (("from_chain", "U"), ("to_chain", "A"))
    encoded = json.dumps(campaign.to_dict())
    for banned in (
        "/tmp",
        "cellertz1",
        "CUDA_VISIBLE_DEVICES",
        "screen -",
        "ssh ",
        " pid",
    ):
        assert banned not in encoded
    assert campaign.target_structure is not None
    assert campaign.target_structure.source_identifier == "7O52"
    identity = campaign.prepared_target_identity()
    assert identity is not None
    assert "reference" not in json.dumps(identity)
    assert identity["prepared_content_digest"] is None


def test_generic_request_does_not_become_a_vhh_campaign() -> None:
    projected = project_vhh_campaign(_request("CD22"))
    assert projected.completeness == "incomplete"
    assert projected.specification is None
    assert projected.execution_authorized is False
    assert "target_structure" in projected.missing_fields
    assert "epitope" in projected.missing_fields
    assert "scaffold" in projected.missing_fields
    assert "generator_version" in projected.missing_fields
    assert "7O52" not in json.dumps(projected.to_dict())
    partial = VHHCampaignSpecification(
        campaign_id="campaign_partial",
        request_id="request_cd22",
        session_id="session_vhh",
        target_id="CD22",
        provenance=_PROVENANCE,
        completeness="incomplete",
    )
    assert partial.target_structure is None
    assert partial.epitope is None
    assert "missing_target_structure" in partial.issues
    assert "missing_epitope" in partial.issues
    assert "missing_generator_version" in partial.issues


def test_mber_filters_stay_out_of_ranking_and_boltz() -> None:
    campaign = cd22_operational_reference()
    assert campaign.acceptance_policy is not None
    metrics = {item.metric_name: item for item in campaign.acceptance_policy.metrics}
    assert metrics["i_ptm"].threshold == 0.75
    assert metrics["i_ptm"].role == "generator_filter"
    assert metrics["plddt"].role == "generator_filter"
    encoded = json.dumps(campaign.to_dict())
    assert "ranking_score" not in encoded
    assert "composite_score" not in encoded
    assert "boltz" not in encoded.casefold()
    with pytest.raises(ApplicationContractError):
        AcceptanceMetric("ranking_score", "gte", 0.5, "ranking", "generator_filter", _PROVENANCE)
    with pytest.raises(ApplicationContractError):
        AcceptanceMetric("boltz_score", "gte", 0.5, "evidence", "independent_evidence", _PROVENANCE)
    with pytest.raises(ApplicationContractError):
        AcceptanceMetric("composite_score", "gte", 0.5, "ranking", "generator_filter", _PROVENANCE)
    zero = AcceptanceMetric(
        "net_charge", "lte", 0, "developability", "developability_evidence", _PROVENANCE
    )
    negative = AcceptanceMetric(
        "delta_score", "gte", -1.5, "developability", "developability_evidence", _PROVENANCE
    )
    assert zero.threshold == 0
    assert negative.threshold == -1.5
    for threshold in (float("nan"), float("inf")):
        with pytest.raises(ApplicationContractError) as invalid:
            AcceptanceMetric(
                "net_charge",
                "lte",
                threshold,
                "developability",
                "developability_evidence",
                _PROVENANCE,
            )
        assert invalid.value.code == "invalid_metric_threshold"


def test_evidence_stages_do_not_promote_each_other() -> None:
    campaign = cd22_operational_reference()
    recorded = {item.stage: item.recorded for item in campaign.evidence}
    assert recorded["generator_accepted"] is False
    assert recorded["scientifically_reviewed"] is False
    assert recorded["independently_evaluated"] is False
    assert recorded["developability_screened"] is False
    assert recorded["off_target_screened"] is False
    one = CampaignEvidence("generator_accepted", True, _PROVENANCE)
    assert one.recorded is True
    assert one.stage != "scientifically_reviewed"
    assert not hasattr(campaign, "advance")
    assert not hasattr(campaign, "transition")


def test_campaign_cannot_be_authorized_and_version_stays_unknown() -> None:
    campaign = cd22_operational_reference()
    assert campaign.execution_authorized is False
    assert campaign.executable is False
    assert campaign.to_dict()["execution_authorized"] is False
    assert campaign.generator_protocol is not None
    assert campaign.generator_protocol.generator_version is None
    assert campaign.generator_protocol.generator_version_status == "not_recorded"
    assert campaign.generator_protocol.to_dict()["generator_version"] is None
    assert campaign.to_dict()["reproducible_readiness"] == "not_ready"
    with pytest.raises(ApplicationContractError) as caught:
        VHHCampaignSpecification(
            campaign_id="campaign_no",
            request_id="request_cd22",
            session_id="session_vhh",
            target_id="CD22",
            provenance=_PROVENANCE,
            completeness="incomplete",
            execution_authorized=True,
        )
    assert caught.value.code == "campaign_is_not_authorized"
    with pytest.raises(ApplicationContractError) as executable:
        VHHCampaignSpecification(
            campaign_id="campaign_no",
            request_id="request_cd22",
            session_id="session_vhh",
            target_id="CD22",
            provenance=_PROVENANCE,
            completeness="incomplete",
            executable=True,
        )
    assert executable.value.code == "campaign_is_not_authorized"
    from cellertz.application.vhh_campaign import GeneratorProtocol

    with pytest.raises(ApplicationContractError) as version:
        GeneratorProtocol(
            protocol_id="mber-vhh-operational",
            generator_name="mber",
            design_modality="vhh",
            algorithm_family="alphafold_multimer_backprop",
            scaffold_mode="fixed_masked_vhh",
            evaluation_strategy="independent_refold",
            provenance=_PROVENANCE,
            generator_version="latest",
        )
    assert version.value.code == "generator_version_must_stay_unknown"


def test_artifact_location_is_not_prepared_target_identity() -> None:
    from dataclasses import replace

    for reference in (
        "artifact://prepared-target/cd22",
        "s3://bucket/prepared/cd22",
        "./input/cd22_mber.pdb",
        "file-relative-reference",
        "artifact://cache/cuda-weights",
        "relative\\cd22.pdb",
    ):
        artifact = ArtifactReference(reference, "prepared_structure", _PROVENANCE)
        assert artifact.reference == reference
        assert artifact.content_digest is None
    with pytest.raises(ApplicationContractError) as credential:
        ArtifactReference("s3://key:secret@bucket/cd22", "prepared_structure", _PROVENANCE)
    assert credential.value.code == "artifact_reference_embeds_credential_or_runtime"
    with pytest.raises(ApplicationContractError) as command:
        ArtifactReference("bash -c export", "prepared_structure", _PROVENANCE)
    assert command.value.code == "artifact_reference_embeds_credential_or_runtime"

    cd22 = cd22_operational_reference()
    structure = cd22.target_structure
    assert structure is not None
    assert structure.original_structure is not None
    assert structure.prepared_structure is not None
    relocated = replace(
        structure,
        original_structure=replace(
            structure.original_structure, reference="s3://bucket/pdb/7O52.pdb"
        ),
        prepared_structure=replace(
            structure.prepared_structure, reference="artifact://prepared-target/cd22"
        ),
    )
    moved = replace(cd22, target_structure=relocated)
    assert moved.prepared_target_identity() == cd22.prepared_target_identity()
    digested = replace(
        structure,
        prepared_structure=replace(structure.prepared_structure, content_digest="sha256:abc"),
    )
    assert replace(cd22, target_structure=digested).prepared_target_identity() != (
        cd22.prepared_target_identity()
    )
    other_preparation = replace(
        structure,
        prepared_target_chain_ids=("B",),
        chain_mapping=(ChainMapping("U", "B"),),
    )
    other = replace(cd22, target_structure=other_preparation)
    assert other.target_structure is not None
    assert other.target_structure.source_identifier == "7O52"
    assert other.prepared_target_identity() != cd22.prepared_target_identity()
    with pytest.raises(ApplicationContractError) as path_identity:
        replace(structure, source_identifier="pdb/7O52")
    assert path_identity.value.code == "structure_identity_is_not_a_path"


def test_taci_prepared_chain_a_is_not_source_partner_a() -> None:
    taci = taci_operational_reference()
    structure = taci.target_structure
    assert structure is not None
    assert structure.source_target_chain_ids == ("R",)
    assert structure.source_partner_chain_ids == ("A", "B", "D")
    assert structure.prepared_target_chain_ids == ("A",)
    assert structure.chain_mapping == (ChainMapping("R", "A"),)
    source_a = ChainRef("source", "A")
    prepared_a = ChainRef("prepared", "A")
    assert source_a != prepared_a
    assert taci.epitope is not None
    assert taci.epitope.residues[0].chain_namespace == "prepared"
    assert taci.epitope.residues[0].chain_id == "A"
    assert source_a in taci.epitope.partner_chains
    assert all(partner.namespace == "source" for partner in taci.epitope.partner_chains)
    identity = taci.prepared_target_identity()
    assert identity is not None
    assert identity["chain_mapping"] == [
        {"source_chain_id": "R", "prepared_chain_id": "A"}
    ]
    assert identity["source_partner_chain_ids"] == ["A", "B", "D"]
    assert identity["prepared_target_chain_ids"] == ["A"]
    assert identity["source_structure"]["source_identifier"] == "1XU1"
    same_letter = ResidueReference("A", 78, chain_namespace="source")
    assert same_letter.key() != taci.epitope.residues[0].key()


def test_unknown_generator_version_blocks_reproducible_readiness() -> None:
    from dataclasses import replace

    campaign = cd22_operational_reference()
    assert campaign.provenance.kind == "operational_reference"
    assert campaign.generator_protocol is not None
    assert campaign.generator_protocol.generator_version is None
    assert campaign.completeness == "incomplete"
    assert "missing_generator_version" in campaign.issues
    assert "generator_version" in project_vhh_campaign(_request()).missing_fields
    recorded = replace(
        campaign.generator_protocol, generator_version="recorded-for-contract-test"
    )
    specified = replace(campaign, generator_protocol=recorded, completeness="complete")
    assert specified.issues == ()
    assert specified.reproducible_readiness == "version_recorded"
    assert specified.execution_authorized is False
    assert specified.executable is False
    assert specified.to_dict()["execution_authorized"] is False


def test_modules_do_not_execute_or_write_scientific_memory() -> None:
    root = Path("src/cellertz/application")
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "vhh_campaign.py", root / "vhh_campaign_fixtures.py")
    )
    for banned in (
        "ProteinSupervisor",
        "ProteinRunStarter",
        "ScientificRunMemory",
        "cellertz.execution",
        "cellertz.runs",
        "CUDA_VISIBLE_DEVICES",
        "screen -dmS",
        "/tmp",
    ):
        assert banned not in text
