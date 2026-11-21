"""The CD22 catalog target: three steps the recorded preparation did not have.

Running the recorded four steps alone leaves a glycerol, a glycan and two
sulfates in the result, which the generator's engine cannot model. What is
worth pinning is that the extra steps are declared rather than folded into the
recorded specification, and that the manifest says which were which.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cellertz.application.cd22_catalog_preparation import (
    EPITOPE_PROXIMITY_ANGSTROM,
    REMOVED_HETEROATOMS,
    catalog_steps,
    write_catalog_target,
)
from cellertz.application.vhh_campaign_fixtures import cd22_operational_reference

EPITOPE = sorted(
    residue.residue_number for residue in cd22_operational_reference().epitope.residues
)


def _atom(serial: int, resname: str, chain: str, resseq: int, record: str = "ATOM  ") -> str:
    return (
        f"{record}{serial:5d}  CA  {resname} {chain}{resseq:4d}"
        f"    {0.0:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00  0.00           C"
    )


def _source() -> str:
    """Shaped like 7O52: CD22 on chain U, the m971 Fab on H and L, plus junk."""

    lines = [_atom(index, "ALA", "U", number) for index, number in enumerate(EPITOPE, start=1)]
    lines += [
        _atom(900, "HOH", "U", 900, record="HETATM"),
        _atom(901, "NAG", "U", 701, record="HETATM"),
        _atom(902, "SO4", "U", 702, record="HETATM"),
        _atom(903, "GOL", "U", 704, record="HETATM"),
        _atom(904, "SER", "H", 10),
        _atom(905, "THR", "L", 11),
    ]
    return "\n".join(lines)


def test_the_catalog_target_has_no_heteroatom_left(tmp_path: Path) -> None:
    manifest = write_catalog_target(_source(), tmp_path)

    written = (tmp_path / "target.pdb").read_text(encoding="utf-8")
    assert "HETATM" not in written
    assert manifest["result"]["heteroatom_residues"] == []
    assert manifest["result"]["chains"] == ["A"]
    for resname in REMOVED_HETEROATOMS:
        assert resname not in written


def test_every_recorded_epitope_residue_survives(tmp_path: Path) -> None:
    write_catalog_target(_source(), tmp_path)

    written = (tmp_path / "target.pdb").read_text(encoding="utf-8")
    present = {
        int(line[22:26])
        for line in written.splitlines()
        if line.startswith("ATOM") and line[21:22] == "A"
    }
    assert set(EPITOPE) <= present
    assert len(EPITOPE) == 24


def test_the_manifest_separates_what_was_recorded_from_what_we_added(tmp_path: Path) -> None:
    """Editing the specification would change what the record says someone did."""

    manifest = json.loads(
        (write_catalog_target(_source(), tmp_path) and (tmp_path / "preparation.json")).read_text(
            encoding="utf-8"
        )
    )

    recorded = [step["operation"] for step in manifest["recorded_steps"]]
    added = [step["operation"] for step in manifest["additional_steps"]]
    assert recorded == ["remove_solvent", "extract_chain", "relabel_chain", "set_segid"]
    assert added == ["remove_selected_heteroatoms"] * 3
    # One sequence actually ran, and it is numbered contiguously.
    assert [step["order"] for step in manifest["executed"]] == list(range(1, 8))
    assert manifest["epitope_residues"] == EPITOPE
    assert set(manifest["epitope_proximity_angstrom"]) == set(REMOVED_HETEROATOMS)


def test_the_digests_are_of_the_bytes(tmp_path: Path) -> None:
    source = _source()
    manifest = write_catalog_target(source, tmp_path)

    assert manifest["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    written = (tmp_path / "target.pdb").read_bytes()
    assert manifest["prepared_sha256"] == hashlib.sha256(written).hexdigest()


def test_each_removal_is_one_named_step() -> None:
    """A single step listing three resnames would not say which ran."""

    steps = catalog_steps()

    assert len(steps) == len(REMOVED_HETEROATOMS)
    assert [dict(step.parameters)["resname"] for step in steps] == list(REMOVED_HETEROATOMS)
    assert all(step.operation == "remove_selected_heteroatoms" for step in steps)


def test_a_source_without_the_target_chain_is_refused(tmp_path: Path) -> None:
    only_fab = "\n".join([_atom(1, "SER", "H", 10), _atom(2, "THR", "L", 11)])

    with pytest.raises(Exception) as refused:
        write_catalog_target(only_fab, tmp_path)
    assert "removed_everything" in str(refused.value)
    assert not (tmp_path / "target.pdb").exists(), "nothing is written on a refusal"


def test_the_measured_proximity_is_recorded_not_guessed() -> None:
    """The numbers that justified the removal, kept beside the decision."""

    assert EPITOPE_PROXIMITY_ANGSTROM["SO4"] < 3.0, "inside hydrogen-bonding distance"
    assert EPITOPE_PROXIMITY_ANGSTROM["NAG"] < 5.0, "the glycan is on the epitope face"
    assert EPITOPE_PROXIMITY_ANGSTROM["GOL"] > 10.0
