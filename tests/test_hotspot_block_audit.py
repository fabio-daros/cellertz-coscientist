"""What a declared hotspot block actually reached, measured from the PDB.

A block is a claim until this runs. The numbers come from heavy-atom distance
in the verified file, never from a review field and never from the agent.
"""

from __future__ import annotations

from cellertz.application.attempt_evidence import (
    AttemptFiles,
    audit_hotspot_blocks,
    geometric_contacts,
    load_catalog,
    workspace_root,
)


def catalog() -> dict:
    root = workspace_root()
    return load_catalog(
        (
            AttemptFiles(
                review_json=root / "src" / "manual-attempt-2LUTUOX4.json",
                pdb_dir=root / "public" / "manual-review" / "attempt.2LUTUOX4",
            ),
        )
    )


def item() -> dict:
    return catalog()["attempt.2LUTUOX4"]


def reached() -> list[int]:
    contacts = geometric_contacts(item())
    return contacts["models"]["model1"]["calculated"]["target_residues"]


def test_contacts_now_name_the_target_residues_not_only_their_count() -> None:
    contacts = geometric_contacts(item())
    calculated = contacts["models"]["model1"]["calculated"]
    assert len(calculated["target_residues"]) == calculated["target_residues_in_contact"]
    assert calculated["target_residues"] == sorted(calculated["target_residues"])


def test_a_block_of_reached_residues_is_fully_contacted() -> None:
    hit = reached()
    block = {"id": "A", "residues": [f"A{number}" for number in hit[:3]]}
    audited = audit_hotspot_blocks([block], item())["blocks"][0]
    assert audited["declared"] == 3
    assert audited["contact_n"] == 3
    assert audited["contact_pct"] == 100
    assert audited["contacted_residues"] == sorted(hit[:3])


def test_a_block_the_designs_never_reached_is_reported_as_zero() -> None:
    hit = set(reached())
    absent = [number for number in range(18, 133) if number not in hit][:4]
    block = {"id": "B", "residues": [f"A{number}" for number in absent]}
    audited = audit_hotspot_blocks([block], item())["blocks"][0]
    assert audited["declared"] == len(absent)
    assert audited["contact_n"] == 0
    assert audited["contact_pct"] == 0
    assert audited["contacted_residues"] == []


def test_a_partly_reached_block_reports_which_residues_were_reached() -> None:
    hit = reached()
    absent = next(number for number in range(18, 133) if number not in set(hit))
    block = {"id": "C", "residues": [f"A{hit[0]}", f"A{absent}"]}
    audited = audit_hotspot_blocks([block], item())["blocks"][0]
    assert (audited["contact_n"], audited["declared"]) == (1, 2)
    assert audited["contact_pct"] == 50
    assert audited["contacted_residues"] == [hit[0]]


def test_an_unverified_structure_refuses_the_audit_instead_of_reporting_zero() -> None:
    broken = item()
    broken["pdb_verified"] = False
    audited = audit_hotspot_blocks([{"id": "A", "residues": ["A56"]}], broken)
    assert audited["status"] == "hash_mismatch"
    assert audited["blocks"] == []
