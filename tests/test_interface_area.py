"""Buried interface area, measured with Shrake-Rupley.

Two chains that never touch must bury nothing; a real complex must bury a
plausible interface. The checks are analytic where they can be.
"""

from __future__ import annotations

import pytest

from cellertz.domains.protein_design.scoring.interface_area import (
    InterfaceAreaError,
    interface_buried_area,
)

PDB = (
    "apps/protein-design-workspace/public/manual-review/attempt.2LUTUOX4/model1.pdb"
)


def atom(serial: int, chain: str, resseq: int, x: float) -> str:
    return (
        f"ATOM  {serial:>5}  CA  ALA {chain}{resseq:>4}    "
        f"{x:>8.3f}{0.0:>8.3f}{0.0:>8.3f}  1.00  0.00           C"
    )


def test_a_real_complex_buries_a_plausible_interface() -> None:
    measured = interface_buried_area(PDB)
    assert measured["delta_sasa_angstrom2"] == pytest.approx(
        sum(measured["per_chain_buried"].values()), abs=0.02
    )
    assert measured["buried_area_angstrom2"] == pytest.approx(
        measured["delta_sasa_angstrom2"] / 2, abs=0.02
    )
    # A 65-residue binder against a 115-residue target: hundreds of square
    # angstroms, not tens and not tens of thousands.
    assert 200 < measured["buried_area_angstrom2"] < 2000
    assert all(value > 0 for value in measured["per_chain_buried"].values())


def test_each_chain_is_never_more_exposed_in_the_complex_than_alone() -> None:
    measured = interface_buried_area(PDB)
    for chain, alone in measured["sasa_alone"].items():
        assert measured["sasa_in_complex"][chain] <= alone


def test_the_measurement_is_deterministic() -> None:
    assert interface_buried_area(PDB) == interface_buried_area(PDB)


def test_two_chains_far_apart_bury_nothing(tmp_path) -> None:
    path = tmp_path / "apart.pdb"
    path.write_text(
        "\n".join([atom(1, "A", 1, 0.0), atom(2, "B", 1, 50.0), "END", ""]),
        encoding="utf-8",
    )
    measured = interface_buried_area(path)
    assert measured["buried_area_angstrom2"] == pytest.approx(0.0, abs=0.01)
    assert measured["per_chain_buried"] == {"A": 0.0, "B": 0.0}


def test_two_atoms_in_contact_bury_surface_on_both_sides(tmp_path) -> None:
    path = tmp_path / "touching.pdb"
    path.write_text(
        "\n".join([atom(1, "A", 1, 0.0), atom(2, "B", 1, 3.0), "END", ""]),
        encoding="utf-8",
    )
    measured = interface_buried_area(path)
    assert measured["buried_area_angstrom2"] > 0
    # Two identical atoms bury comparable area, not identical area: the sphere
    # is sampled at 100 fixed points, so translating an atom moves which
    # points fall inside its neighbour. A couple of square angstroms on a
    # ~30 square angstrom patch is that sampling, not an asymmetry.
    assert measured["per_chain_buried"]["A"] == pytest.approx(
        measured["per_chain_buried"]["B"], abs=2.0
    )


def test_an_absent_chain_is_named_rather_than_measured_as_zero(tmp_path) -> None:
    path = tmp_path / "one_chain.pdb"
    path.write_text("\n".join([atom(1, "A", 1, 0.0), "END", ""]), encoding="utf-8")
    with pytest.raises(InterfaceAreaError) as error:
        interface_buried_area(path)
    assert str(error.value) == "chain_absent:B"


def test_a_missing_file_is_refused() -> None:
    with pytest.raises(InterfaceAreaError) as error:
        interface_buried_area("/nonexistent/model.pdb")
    assert str(error.value) == "structure_not_found"
