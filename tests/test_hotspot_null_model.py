"""Reachability and chance, the two things the atlas's re-audit added.

Without them "median 3 of 9 hotspots" has no denominator and nothing to
compare against. The atlas's own numbers are the shape being reproduced: a
buried hotspot leaves the denominator, and an unseeded arm at 2.95x with 8.7%
of designs individually clear is a different claim from a seeded arm at 9x.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cellertz.domains.protein_design.scoring.hotspot_null_model import (
    BURIED_RELATIVE_SASA,
    CONTACT_ANGSTROM,
    MAX_ASA,
    audit_against_null,
    hotspot_null_model,
    reachable_hotspots,
    relative_sasa,
)
from cellertz.domains.protein_design.scoring.interface_area import InterfaceAreaError

MODEL = Path("apps/protein-design-workspace/public/manual-review/attempt.2LUTUOX4/model1.pdb")


def _sphere(path: Path, count: int = 80) -> Path:
    """A shell of glycines plus one residue buried at the centre.

    Spread on a Fibonacci sphere so every shell residue is genuinely exposed
    and the centre one genuinely is not. Real structures are the better test
    of the SASA call, but a known-geometry case is the only way to pin what
    "buried" means.
    """

    import math

    lines = []
    serial = 1
    for index in range(count):
        y = 1 - 2 * (index + 0.5) / count
        radius = math.sqrt(max(0.0, 1 - y * y))
        angle = math.pi * (1 + 5**0.5) * index
        x, z = math.cos(angle) * radius, math.sin(angle) * radius
        lines.append(
            f"ATOM  {serial:5d}  CA  GLY A{index + 1:4d}    "
            f"{x * 6:8.3f}{y * 6:8.3f}{z * 6:8.3f}  1.00  0.00           C"
        )
        serial += 1
    lines.append(
        f"ATOM  {serial:5d}  CA  GLY A{count + 1:4d}    "
        f"{0.0:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00  0.00           C"
    )
    path.write_text("\n".join(lines) + "\nEND\n", encoding="utf-8")
    return path


def test_the_contact_definition_is_the_one_the_atlas_states() -> None:
    assert CONTACT_ANGSTROM == 5.0


def test_relative_sasa_separates_a_shell_from_a_buried_centre(tmp_path: Path) -> None:
    structure = _sphere(tmp_path / "shell.pdb")

    measured = relative_sasa(structure, "A")
    relative = measured["relative"]

    centre = max(relative)
    assert relative[centre] < BURIED_RELATIVE_SASA, "the centre is enclosed"
    shell = [value for number, value in relative.items() if number != centre]
    assert min(shell) > BURIED_RELATIVE_SASA
    assert measured["no_reference_area"] == []
    assert measured["max_asa_reference"] == "tien_2013_theoretical"


def test_a_buried_hotspot_leaves_the_denominator(tmp_path: Path) -> None:
    """GPC3's ILE215 at 0.018: the mode moved from 8 of 9 to 7 of 8."""

    structure = _sphere(tmp_path / "shell.pdb")
    centre = 81

    split = reachable_hotspots(structure, [1, 2, 3, centre], "A")

    assert split["buried"] == [centre]
    assert split["reachable"] == [1, 2, 3]
    assert split["not_in_structure"] == []
    assert split["relative_sasa"][centre] < BURIED_RELATIVE_SASA


def test_a_declared_residue_absent_from_the_structure_is_named(tmp_path: Path) -> None:
    structure = _sphere(tmp_path / "shell.pdb")

    split = reachable_hotspots(structure, [1, 9999], "A")

    assert split["not_in_structure"] == [9999]
    assert split["reachable"] == [1]


def test_the_declared_share_of_surface_is_reported(tmp_path: Path) -> None:
    """The atlas's caveat: on TACI 44% of the surface returns 1.86 and says little."""

    structure = _sphere(tmp_path / "shell.pdb")

    narrow = reachable_hotspots(structure, [1, 2, 3], "A")
    wide = reachable_hotspots(structure, list(range(1, 41)), "A")

    assert narrow["declared_share_of_surface"] < 0.1
    assert wide["declared_share_of_surface"] > 0.4


def test_the_null_model_is_reproducible_and_records_its_seed(tmp_path: Path) -> None:
    """A null model whose seed is not written down cannot be re-checked."""

    structure = _sphere(tmp_path / "shell.pdb")

    first = hotspot_null_model(structure, [1, 2, 3], 8, "A", draws=200, seed=7)
    again = hotspot_null_model(structure, [1, 2, 3], 8, "A", draws=200, seed=7)
    other = hotspot_null_model(structure, [1, 2, 3], 8, "A", draws=200, seed=8)

    assert first == again
    assert first["seed"] == 7 and first["draws"] == 200
    assert first != other, "a different seed draws different patches"
    assert 0.0 <= first["mean_recall"] <= 1.0
    assert first["p95_recall"] >= first["mean_recall"]


def test_a_bigger_footprint_has_a_better_chance(tmp_path: Path) -> None:
    """Which is the whole point of matching the patch to the design's size."""

    structure = _sphere(tmp_path / "shell.pdb")

    small = hotspot_null_model(structure, [1, 2, 3], 4, "A", draws=200, seed=3)
    large = hotspot_null_model(structure, [1, 2, 3], 30, "A", draws=200, seed=3)

    assert large["mean_recall"] > small["mean_recall"]


def test_a_design_on_the_hotspots_beats_its_own_null(tmp_path: Path) -> None:
    structure = _sphere(tmp_path / "shell.pdb")
    hotspots = [1, 2, 3]
    pool = reachable_hotspots(structure, hotspots, "A")

    audit = audit_against_null(
        structure,
        pool["reachable"],
        {"on_target": [1, 2, 3, 10, 11], "off_target": [30, 31, 32, 33, 34]},
        "A",
        draws=200,
        seed=5,
    )

    rows = {row["design_id"]: row for row in audit["rows"]}
    assert rows["on_target"]["recall"] == 1.0
    assert rows["on_target"]["beats_own_null_p05"] is True
    assert rows["off_target"]["recall"] < 1.0
    assert audit["enrichment"] is not None and audit["enrichment"] > 1.0
    assert audit["designs"] == 2
    assert audit["seed"] == 5


def test_the_share_clearing_its_own_null_is_not_the_mean_enrichment(tmp_path: Path) -> None:
    """The atlas reports campaign A at 2.95x with only 8.7% individually clear."""

    structure = _sphere(tmp_path / "shell.pdb")
    footprints = {"hit": [1, 2, 3, 20]}
    footprints.update({f"miss{index}": [40, 41, 42, 43] for index in range(9)})

    audit = audit_against_null(structure, [1, 2, 3], footprints, "A", draws=200, seed=11)

    assert audit["share_beating_own_null_p05"] == pytest.approx(0.1, abs=0.001)
    assert audit["enrichment"] is not None


def test_it_refuses_rather_than_inventing_a_denominator(tmp_path: Path) -> None:
    structure = _sphere(tmp_path / "shell.pdb")

    with pytest.raises(InterfaceAreaError):
        reachable_hotspots(structure, [], "A")
    with pytest.raises(InterfaceAreaError):
        hotspot_null_model(structure, [1], 0, "A")
    with pytest.raises(InterfaceAreaError):
        hotspot_null_model(structure, [1], 4, "A", draws=0)
    with pytest.raises(InterfaceAreaError):
        audit_against_null(structure, [1], {}, "A")
    with pytest.raises(InterfaceAreaError):
        relative_sasa(structure, "Z")


def test_it_works_on_a_structure_that_came_off_the_card() -> None:
    """The shell fixture pins the definition; a real attempt pins the call."""

    measured = relative_sasa(MODEL, "A")

    assert len(measured["relative"]) > 100
    assert all(0.0 <= value <= 1.6 for value in measured["relative"].values())
    assert set(MAX_ASA) >= {"ALA", "GLY", "TRP"}
