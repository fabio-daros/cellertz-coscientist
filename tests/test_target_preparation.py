"""Executing the preparation a specification records.

The operations were names in a record and nothing performed one, so the thing
worth pinning is that each recorded name does what it says, that the recorded
order is the order that runs, and that a step which cannot be performed is a
refusal rather than a no-op.
"""

from __future__ import annotations

import pytest

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.target_preparation import (
    prepare_from_specification,
    prepare_target,
)
from cellertz.application.vhh_campaign import (
    ScientificProvenance,
    TargetPreparationStep,
)
from cellertz.application.vhh_campaign_fixtures import cd22_operational_reference


def _provenance() -> ScientificProvenance:
    return ScientificProvenance("operational_reference", "test", ())


def _step(order: int, operation: str, **parameters: str) -> TargetPreparationStep:
    return TargetPreparationStep(
        order,
        operation,
        tuple(parameters.items()),
        "pdb:source",
        "pdb:output",
        _provenance(),
    )


def _atom(serial: int, resname: str, chain: str, resseq: int, record: str = "ATOM  ") -> str:
    return (
        f"{record}{serial:5d}  CA  {resname} {chain}{resseq:4d}"
        f"    {0.0:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00  0.00           C"
    )


SOURCE = "\n".join(
    [
        _atom(1, "ALA", "U", 624),
        _atom(2, "GLY", "U", 625),
        _atom(3, "HOH", "U", 900, record="HETATM"),
        _atom(4, "NAG", "U", 901, record="HETATM"),
        _atom(5, "SER", "H", 10),
        _atom(6, "THR", "L", 11),
    ]
)


def test_each_recorded_operation_does_what_it_names() -> None:
    prepared = prepare_target(
        SOURCE,
        (
            _step(1, "remove_solvent"),
            _step(2, "remove_selected_heteroatoms", resname="NAG"),
            _step(3, "extract_chain", chain_id="U"),
            _step(4, "relabel_chain", from_chain="U", to_chain="A"),
            _step(5, "set_segid", chain_id="A", segid="A"),
        ),
    )

    assert prepared.chains == ("A",)
    assert prepared.residue_count == 2
    assert prepared.heteroatom_residues == ()
    assert "HOH" not in prepared.pdb and "NAG" not in prepared.pdb
    assert "SER" not in prepared.pdb, "the partner chains are gone"
    # The numbers were measured on the original chain and are kept.
    assert prepared.first_residue == 624
    for line in prepared.pdb.splitlines():
        if line.startswith("ATOM"):
            assert line[72:76] == "A   ", repr(line[72:76])


def test_the_recorded_order_is_the_order_that_runs() -> None:
    """Relabelling before extracting takes a different chain."""

    reordered = prepare_target(
        SOURCE,
        (
            _step(1, "relabel_chain", from_chain="U", to_chain="A"),
            _step(2, "extract_chain", chain_id="A"),
        ),
    )
    assert reordered.chains == ("A",)

    with pytest.raises(ApplicationContractError) as refused:
        prepare_target(
            SOURCE,
            (
                _step(1, "extract_chain", chain_id="A"),
                _step(2, "relabel_chain", from_chain="U", to_chain="A"),
            ),
        )
    assert "removed_everything" in str(refused.value)


def test_a_step_that_cannot_run_is_refused_not_skipped() -> None:
    with pytest.raises(ApplicationContractError) as missing:
        prepare_target(SOURCE, (_step(1, "extract_chain"),))
    assert "missing_chain_id" in str(missing.value)

    with pytest.raises(ApplicationContractError):
        prepare_target(SOURCE, ())

    with pytest.raises(ApplicationContractError):
        prepare_target("REMARK nothing here\n", (_step(1, "remove_solvent"),))


def test_preparation_is_checked_against_what_the_specification_declares() -> None:
    """The CD22 campaign's own recorded steps, on a structure shaped like 7O52."""

    specification = cd22_operational_reference()
    epitope = tuple(residue.residue_number for residue in specification.epitope.residues)
    source = "\n".join(
        [_atom(index, "ALA", "U", number) for index, number in enumerate(epitope, start=1)]
        + [_atom(900, "HOH", "U", 900, record="HETATM"), _atom(901, "SER", "H", 10)]
    )

    prepared = prepare_from_specification(specification, source)

    assert prepared.chains == ("A",)
    assert prepared.residue_count == len(epitope)
    assert prepared.first_residue == min(epitope)
    assert prepared.last_residue == max(epitope)


def test_an_epitope_that_does_not_survive_the_preparation_is_refused() -> None:
    """A hotspot list pointing at residues the prepared file lacks is fiction."""

    specification = cd22_operational_reference()
    epitope = sorted(residue.residue_number for residue in specification.epitope.residues)
    source = "\n".join(
        _atom(index, "ALA", "U", number)
        for index, number in enumerate(epitope[:-3], start=1)
    )

    with pytest.raises(ApplicationContractError) as refused:
        prepare_from_specification(specification, source)

    message = str(refused.value)
    assert "epitope_residues_absent_after_preparation" in message
    for number in epitope[-3:]:
        assert str(number) in message


def test_a_prepared_chain_the_specification_did_not_declare_is_refused() -> None:
    specification = cd22_operational_reference()
    source = "\n".join([_atom(1, "ALA", "Z", 624), _atom(2, "GLY", "Z", 625)])

    with pytest.raises(ApplicationContractError) as refused:
        prepare_from_specification(specification, source)
    assert "removed_everything" in str(refused.value)


def test_a_catalog_root_holding_only_a_target_reports_rather_than_raises(tmp_path) -> None:
    """A prepared target alone is an unverified root, not a crash.

    ``_read_regular`` returns None for an absent file and canonicalising None
    raised AttributeError straight out of ``registration_status``, which is a
    status method. A CD22 directory holds exactly one of the three roles, so
    this is the configuration that reached it.
    """

    from cellertz.application.generation_plan import open_registered_input_catalog

    (tmp_path / "target.pdb").write_text(_atom(1, "ALA", "A", 1) + "\n", encoding="utf-8")
    catalog = open_registered_input_catalog({"CELLERTZ_PDL1_INPUT_ROOT": str(tmp_path)})

    assert catalog.registration_status() == "input_files_unverified"
    assert catalog.identities() is None
    assert catalog.verified_paths() is None
