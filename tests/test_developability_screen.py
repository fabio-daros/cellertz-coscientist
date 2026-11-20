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


def test_a_design_over_the_ligand_footprint_is_rejected_by_that_filter() -> None:
    declared = reached()[: OCCLUSION_LIMIT + 2]
    screened = screen_developability(item(), declared)["models"]["model1"]
    assert screened["occlusion"]["footprint"] == len(declared)
    assert screened["rejected_by"] == "occlusion_footprint"
    assert screened["unscreened"] == []


def test_a_ligand_footprint_inside_the_limit_is_measured_and_passes() -> None:
    declared = reached()[:3]
    screened = screen_developability(item(), declared)["models"]["model1"]
    assert screened["occlusion"] == {
        "status": "measured",
        "ligand_residues": 3,
        "footprint": 3,
        "occluded_residues": declared,
        "limit": OCCLUSION_LIMIT,
    }
    assert screened["rejected_by"] is None


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
