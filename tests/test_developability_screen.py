"""The atlas pre-filters, measured from the verified PDB.

The professor screens before he ranks: sequence liabilities, then how much of
the natural ligand's footprint the design occludes, then net charge. The
sequence and the disulfide pairing are read from the structure, never from a
review field, and an unverified file refuses the screen.
"""

from __future__ import annotations

from cellertz.application.attempt_evidence import (
    AttemptFiles,
    OCCLUSION_LIMIT,
    _cysteine_pairing,
    _glycosylation_sequons,
    geometric_contacts,
    load_catalog,
    screen_developability,
    workspace_root,
)

ATTEMPT = "attempt.2LUTUOX4"


def item() -> dict:
    root = workspace_root()
    return load_catalog(
        (
            AttemptFiles(
                review_json=root / "src" / f"manual-{ATTEMPT.replace('attempt.', 'attempt-')}.json",
                pdb_dir=root / "public" / "manual-review" / ATTEMPT,
            ),
        )
    )[ATTEMPT]


def reached() -> list[int]:
    return geometric_contacts(item())["models"]["model1"]["calculated"]["target_residues"]


def test_the_sequence_comes_out_of_the_structure_at_its_recorded_length() -> None:
    attempt = item()
    screened = screen_developability(attempt)["models"]["model1"]
    assert screened["length"] == attempt["models"]["model1"]["record"]["binder_residues"]
    assert set(screened["sequence"]) <= set("ACDEFGHIKLMNPQRSTVWY")


def test_a_design_with_no_liability_and_a_negative_charge_passes() -> None:
    screened = screen_developability(item())["models"]["model1"]
    assert screened["net_charge"] <= 2
    assert (screened["rejected_by"], screened["verdict"]) == (None, "passed_measured_filters")


def test_occlusion_is_named_unscreened_rather_than_passed_when_no_ligand_is_declared() -> None:
    screened = screen_developability(item())["models"]["model1"]
    assert screened["occlusion"]["status"] == "not_declared"
    assert screened["unscreened"] == ["occlusion_footprint"]


def test_occlusion_is_measured_without_a_limit_and_is_not_a_gate() -> None:
    """Covering the ligand's site is the goal of a blockade campaign.

    The atlas's limit of 8 of 18 comes from a BCMA engager that had to leave
    APRIL's site alone. Rejecting on occlusion by default would throw away
    exactly the designs a PD-L1 blocker is for, so a declared footprint with
    no declared limit is a number and not a filter.
    """

    declared = reached()
    screened = screen_developability(item(), declared)["models"]["model1"]
    assert screened["occlusion"]["footprint"] == len(declared)
    assert screened["occlusion"]["is_a_gate"] is False
    assert screened["occlusion"]["limit"] is None
    assert screened["rejected_by"] is None
    assert screened["unscreened"] == []


def test_a_campaign_that_declares_the_limit_gets_the_atlas_filter_back() -> None:
    declared = reached()[: OCCLUSION_LIMIT + 2]
    screened = screen_developability(item(), declared, occlusion_limit=OCCLUSION_LIMIT)[
        "models"
    ]["model1"]
    assert screened["occlusion"]["is_a_gate"] is True
    assert screened["rejected_by"] == "occlusion_footprint"


def test_a_ligand_footprint_inside_a_declared_limit_passes() -> None:
    declared = reached()[:3]
    screened = screen_developability(item(), declared, occlusion_limit=OCCLUSION_LIMIT)[
        "models"
    ]["model1"]
    assert screened["occlusion"] == {
        "status": "measured",
        "ligand_residues": 3,
        "footprint": 3,
        "occluded_residues": declared,
        "limit": OCCLUSION_LIMIT,
        "is_a_gate": True,
    }
    assert screened["rejected_by"] is None


def test_the_pdl1_footprint_is_translated_into_the_structures_numbering() -> None:
    """The footprint is declared on a file numbered 18 to 132; the attempt
    structures are numbered from 1. The offset is established by the identity
    of all 22 recorded residues, never assumed."""

    from cellertz.application.attempt_evidence import (
        PDL1_PD1_FOOTPRINT,
        native_ligand_footprint,
    )

    footprint = native_ligand_footprint(item())
    assert footprint["status"] == "declared"
    assert footprint["offset_from_declared_frame"] == 17
    assert footprint["residues"] == tuple(number - 17 for number in PDL1_PD1_FOOTPRINT)
    assert footprint["residues_in_declared_frame"] == PDL1_PD1_FOOTPRINT


def test_the_design_on_record_covers_part_of_the_pd1_site() -> None:
    """With the offset applied the design lands on PD-1's interface, which is
    what a PD-L1 blocker is supposed to do. Without it, it would appear to
    bind somewhere else entirely."""

    from cellertz.application.attempt_evidence import native_ligand_footprint

    footprint = native_ligand_footprint(item())
    screened = screen_developability(item(), footprint["residues"])["models"]["model1"]
    assert screened["occlusion"]["ligand_residues"] == 22
    assert screened["occlusion"]["footprint"] == 12


def test_a_structure_whose_identities_do_not_match_refuses_the_footprint(tmp_path) -> None:
    from cellertz.application.attempt_evidence import pdl1_numbering_offset

    # Right length, wrong protein: every residue is alanine.
    lines = [
        f"ATOM  {index:>5}  CA  ALA A{index:>4}    {0.0:>8.3f}{0.0:>8.3f}{0.0:>8.3f}  1.00  0.00           C"
        for index in range(1, 116)
    ]
    assert pdl1_numbering_offset("\n".join(lines)) is None


def test_a_ligand_residue_the_design_never_reached_is_not_counted_as_occluded() -> None:
    hit = set(reached())
    absent = [number for number in range(18, 133) if number not in hit][:5]
    screened = screen_developability(item(), absent)["models"]["model1"]
    assert screened["occlusion"]["footprint"] == 0
    assert screened["rejected_by"] is None


def test_an_unverified_structure_refuses_the_screen() -> None:
    broken = item()
    broken["pdb_verified"] = False
    screened = screen_developability(broken)
    assert screened["status"] == "hash_mismatch"
    assert screened["models"] == {}


def test_a_free_thiol_is_unpaired_and_a_disulfide_is_not() -> None:
    residues = [(1, "C"), (2, "A"), (3, "C"), (4, "C")]
    thiols = {1: (0.0, 0.0, 0.0), 3: (0.0, 0.0, 2.05), 4: (0.0, 0.0, 20.0)}
    pairing = _cysteine_pairing(residues, thiols)
    assert pairing["cysteines"] == [1, 3, 4]
    assert pairing["unpaired"] == [4]
    assert pairing["without_sg_atom"] == []


def test_a_cysteine_whose_thiol_the_file_omits_is_reported_not_assumed_paired() -> None:
    pairing = _cysteine_pairing([(7, "C")], {})
    assert pairing["unpaired"] == [7]
    assert pairing["without_sg_atom"] == [7]


def test_a_sequon_needs_n_x_s_or_t_with_x_not_proline() -> None:
    assert _glycosylation_sequons([(10, "N"), (11, "A"), (12, "T")]) == ["N10-A-T12"]
    assert _glycosylation_sequons([(10, "N"), (11, "P"), (12, "T")]) == []
    assert _glycosylation_sequons([(10, "N"), (11, "A"), (12, "A")]) == []


def test_three_residues_across_a_numbering_break_are_not_a_sequon() -> None:
    assert _glycosylation_sequons([(10, "N"), (40, "A"), (41, "T")]) == []
