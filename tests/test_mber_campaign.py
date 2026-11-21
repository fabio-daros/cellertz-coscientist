"""One path from a specification to a ranked mBER cohort.

The output directory is synthesised with mBER's real accepted.csv header and
real complex structures, so the join is exercised end to end without a GPU.
"""

from __future__ import annotations

import csv
import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.mber_campaign import (
    SETTINGS_FILENAME,
    launch_request,
    rank_mber_output,
)
from cellertz.application.mber_settings import GENERATOR_VERSION
from cellertz.application.vhh_campaign_fixtures import cd22_operational_reference

# The real header, read at the pinned commit of mber-open.
HEADER = [
    "trajectory_name",
    "binder_index",
    "binder_seq",
    "i_ptm",
    "plddt",
    "ptm",
    "complex_pdb_path",
    "relaxed_pdb_path",
]
MODEL = Path(
    "apps/protein-design-workspace/public/manual-review/attempt.2LUTUOX4/model1.pdb"
)
# Chain B of that structure, which is what the screen reads off the file.
BINDER = "MTKYMLFRRLRDREHVGPWQEEMLSEEEAPKREAELKAEAEAMRAESEARGGTGRELEYYVWKAP"
# Target residues in that structure's own numbering; the design reaches part of
# this set, so a cohort ranked against it has a non-trivial J_clin.
CLINICAL = (frozenset({37, 39, 41, 49, 96, 98}),)


def versioned():
    specification = cd22_operational_reference()
    return dataclasses.replace(
        specification,
        generator_protocol=dataclasses.replace(
            specification.generator_protocol, generator_version=GENERATOR_VERSION
        ),
        completeness="complete",
    )


def cohort(tmp_path: Path, designs: list[dict]) -> Path:
    """An mBER output directory: accepted.csv plus Accepted/ structures."""

    accepted = tmp_path / "Accepted"
    accepted.mkdir(parents=True, exist_ok=True)
    rows = []
    for design in designs:
        name = f"{design['trajectory']}_binder-1_complex.pdb"
        shutil.copy(MODEL, accepted / name)
        rows.append(
            {
                "trajectory_name": design["trajectory"],
                "binder_index": "1",
                "binder_seq": design.get("sequence", BINDER),
                "i_ptm": str(design["i_ptm"]),
                "plddt": "0.85",
                "ptm": "0.80",
                "complex_pdb_path": str(accepted / name),
                "relaxed_pdb_path": "",
            }
        )
    with (tmp_path / "accepted.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(rows)
    return tmp_path


def test_the_launch_writes_the_settings_and_names_the_registered_binary(tmp_path) -> None:
    request = launch_request(
        versioned(), "/inputs/cd22_prepared.pdb", tmp_path, mber_bin="/opt/mber/bin/mber-vhh"
    )
    written = json.loads((tmp_path / SETTINGS_FILENAME).read_text(encoding="utf-8"))
    assert written["target"]["name"] == "CD22"
    assert request.generator == "mber"
    assert request.constraints["mber_command"][0] == "/opt/mber/bin/mber-vhh"
    assert request.constraints["mber_command"][1] == "--settings"
    assert request.constraints["mber_version"] == GENERATOR_VERSION
    # The stopping policy decides the cohort size, not a caller's guess.
    assert request.n_candidates == 5000


def test_a_cohort_is_screened_then_ranked(tmp_path) -> None:
    directory = cohort(
        tmp_path,
        [
            {"trajectory": "traj_a", "i_ptm": 0.95},
            {"trajectory": "traj_b", "i_ptm": 0.88},
            {"trajectory": "traj_c", "i_ptm": 0.83},
        ],
    )
    report = rank_mber_output(versioned(), directory, CLINICAL)
    assert report["generated"] == 3
    assert report["screened_in"] == 3
    assert report["not_measurable"] == []
    assert report["generator_version"] == GENERATOR_VERSION
    ranked = report["ranking"]["designs"]
    assert len(ranked) == 3
    assert all(row["protocol_id"] == "ENGAGER_TWO_AXIS_2026" for row in ranked)
    assert all(row["score"] == min(row["i_ptm_percentile"], row["composite_percentile"]) for row in ranked)


def test_the_screen_runs_before_the_ranking_and_its_rejects_never_reach_it(tmp_path) -> None:
    """His order. A design the pre-filters reject is not in the cohort whose
    percentiles are computed, which is the point of filtering first."""

    directory = cohort(tmp_path, [{"trajectory": "traj_a", "i_ptm": 0.95}])
    # A charge above +2 is the last pre-filter in the atlas's order.
    report = rank_mber_output(
        versioned(), directory, CLINICAL, ligand_residues=(37, 39), occlusion_limit=1
    )
    assert report["screened_in"] == 0
    assert report["rejected_by_screen"][0]["rejected_by"] == "occlusion_footprint"
    assert report["ranking"]["status"] == "no_design_survived_the_screen"


def test_a_design_below_the_iptm_gate_is_rejected_by_the_protocol(tmp_path) -> None:
    directory = cohort(
        tmp_path,
        [{"trajectory": "traj_a", "i_ptm": 0.95}, {"trajectory": "traj_b", "i_ptm": 0.50}],
    )
    report = rank_mber_output(versioned(), directory, CLINICAL)
    assert report["screened_in"] == 2
    ranking = report["ranking"]
    # Both pass the pre-filters; only the one above 0.80 reaches a rank.
    assert [row["i_ptm"] for row in ranking["designs"]] == [0.95]
    assert len(ranking["rejected_by_gate"]) == 1
    assert "traj_b" in ranking["rejected_by_gate"][0]


def test_a_sequence_that_disagrees_with_its_structure_is_not_ranked(tmp_path) -> None:
    """The charge and the liabilities are read off the structure; the ranked
    sequence comes from the CSV. If they differ they are different molecules."""

    directory = cohort(
        tmp_path, [{"trajectory": "traj_a", "i_ptm": 0.95, "sequence": "A" * len(BINDER)}]
    )
    report = rank_mber_output(versioned(), directory, CLINICAL)
    assert report["screened_in"] == 0
    assert report["not_measurable"][0]["reason"] == "sequence_does_not_match_structure"


def test_a_missing_structure_is_named_rather_than_dropped(tmp_path) -> None:
    """A design that vanishes from a cohort changes every percentile in it."""

    directory = cohort(tmp_path, [{"trajectory": "traj_a", "i_ptm": 0.95}])
    for structure in (directory / "Accepted").glob("*.pdb"):
        structure.unlink()
    report = rank_mber_output(versioned(), directory, CLINICAL)
    assert report["not_measurable"][0]["reason"] == "complex_structure_not_found"
    assert report["screened_in"] == 0


def test_the_buried_area_used_is_the_one_the_occlusion_pass_measured(tmp_path) -> None:
    """One surface pass per design, and its number is the composite's term."""

    from cellertz.domains.protein_design.scoring.interface_area import (
        interface_buried_area,
        occluded_by_partners,
    )

    directory = cohort(tmp_path, [{"trajectory": "traj_a", "i_ptm": 0.95}])
    structure = next((directory / "Accepted").glob("*.pdb"))
    occlusion = occluded_by_partners(structure, "A", ("B",))
    per_chain = interface_buried_area(structure, target_chain="A", binder_chain="B")
    assert occlusion["total_occluded_area_angstrom2"] == pytest.approx(
        per_chain["per_chain_buried"]["A"], abs=1.0
    )
    report = rank_mber_output(versioned(), directory, CLINICAL)
    assert report["ranking"]["designs"][0]["terms"]["buried_area"] == pytest.approx(
        occlusion["total_occluded_area_angstrom2"]
    )


def test_an_output_directory_without_accepted_csv_is_refused(tmp_path) -> None:
    with pytest.raises(ApplicationContractError) as error:
        rank_mber_output(versioned(), tmp_path, CLINICAL)
    assert str(error.value) == "mber_output_has_no_accepted_csv"


def test_an_unversioned_campaign_cannot_even_be_launched(tmp_path) -> None:
    with pytest.raises(ApplicationContractError) as error:
        launch_request(cd22_operational_reference(), "/inputs/t.pdb", tmp_path)
    assert str(error.value) == "missing_generator_version"
