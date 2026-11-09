"""The workspace preview projects the 6C.1 fixtures. It does not revalidate them."""

from __future__ import annotations

import json
from pathlib import Path

from cellertz.application.vhh_campaign_fixtures import (
    cd22_operational_reference,
    taci_operational_reference,
)

_PREVIEW = Path("apps/protein-design-workspace/src/campaign-fixtures.json")


def test_preview_matches_operational_references() -> None:
    payload = json.loads(_PREVIEW.read_text(encoding="utf-8"))
    assert payload["contract_version"] == "vhh-campaign-6c1"
    assert payload["provenance_kind"] == "operational_reference"
    shown = {item["campaign_id"]: item for item in payload["campaigns"]}
    expected = {
        "cd22-operational": cd22_operational_reference(),
        "taci-operational": taci_operational_reference(),
    }
    assert set(shown) == set(expected)
    for campaign_id, spec in expected.items():
        preview = shown[campaign_id]
        data = spec.to_dict()
        structure = data["target_structure"]
        assert preview["source_identifier"] == structure["source_identifier"]
        assert preview["source_target_chain_ids"] == structure["source_target_chain_ids"]
        assert preview["source_partner_chain_ids"] == structure["source_partner_chain_ids"]
        assert preview["prepared_target_chain_ids"] == structure["prepared_target_chain_ids"]
        assert preview["chain_mapping"] == structure["chain_mapping"]
        assert preview["preparation"] == [
            {
                "order": step["order"],
                "operation": step["operation"],
                "parameters": step["parameters"],
            }
            for step in data["preparation"]
        ]
        assert preview["epitope"]["contact_cutoff_angstrom"] == data["epitope"]["contact_cutoff_angstrom"]
        assert preview["epitope"]["residues"] == [
            {
                "chain_namespace": residue["chain_namespace"],
                "chain_id": residue["chain_id"],
                "residue_number": residue["residue_number"],
            }
            for residue in data["epitope"]["residues"]
        ]
        assert preview["epitope"]["partner_chains"] == data["epitope"]["partner_chains"]
        assert preview["scaffold"]["masked_sequence"] == data["scaffold"]["masked_sequence"]
        assert preview["metrics"] == [
            {
                "metric_name": metric["metric_name"],
                "direction": metric["direction"],
                "threshold": metric["threshold"],
                "role": metric["role"],
            }
            for metric in data["acceptance_policy"]["metrics"]
        ]
        assert preview["accepted_design_target"] == data["stopping_policy"]["accepted_design_target"]
        assert preview["max_trajectories"] == data["stopping_policy"]["max_trajectories"]
        assert preview["generator_version"] is None
        assert preview["generator_version_status"] == "not_recorded"
        assert preview["execution_authorized"] is False
        assert preview["executable"] is False
        assert preview["reproducible_readiness"] == "not_ready"
        assert preview["candidate_count"] is None
        assert preview["candidate_count"] != 0
        structure = data["target_structure"]
        assert preview["preparation_status"] == structure["preparation_status"]
        assert preview["preparation_note"] == structure["preparation_provenance"]["note"]
        assert preview["original_artifact"] == structure["original_structure"]["reference"]
        assert preview["prepared_artifact"] == structure["prepared_structure"]["reference"]
        assert preview["prepared_content_digest"] == structure["prepared_structure"]["content_digest"]
        assert preview["epitope"]["biological_rationale"] == data["epitope"]["biological_rationale"]
        scaffold = data["scaffold"]
        assert preview["scaffold"]["scaffold_id"] == scaffold["scaffold_id"]
        assert preview["scaffold"]["scaffold_version"] == scaffold["scaffold_version"]
        assert preview["scaffold"]["mutable_regions"] == scaffold["mutable_regions"]
        assert preview["scaffold"]["cdr_regions"] == scaffold["cdr_regions"]
        assert preview["scaffold"]["provenance_kind"] == scaffold["provenance"]["kind"]
        protocol = data["generator_protocol"]
        assert preview["generator_name"] == protocol["generator_name"]
        assert preview["protocol_id"] == protocol["protocol_id"]
        assert preview["protocol_version"] == protocol["protocol_version"]
        assert preview["completeness"] == data["completeness"]
        assert preview["issues"] == data["issues"]
        assert preview["provenance_kind"] == data["provenance"]["kind"]
        assert preview["provenance_note"] == data["provenance"]["note"]
        assert "n_candidates" not in data
        assert [(item["stage"], item["recorded"]) for item in preview["evidence"]] == [
            (item["stage"], False) for item in data["evidence"]
        ]
