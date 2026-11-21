"""The classification a declared block gets against the clinical partner.

This is the measurement the atlas makes before spending GPU and the platform
did not make at all: the front end's every block ended in "Contact with this
block is not measured yet."

The numbers below are the atlas's own, recomputed from the coordinates it
embeds. Two of its records disagree with themselves — cd16a block C states
7 of 10 is 50% and gpc3 block B states 5 of 12 is 100% — so the fixtures here
use the records that do not.
"""

from __future__ import annotations

import pytest

from cellertz.domains.protein_design.scoring.interface_area import InterfaceAreaError
from cellertz.domains.protein_design.scoring.partner_classification import (
    EDGE_CONTACT_PCT,
    VERDICTS,
    block_verdict,
    classify_blocks,
    heavy_atoms,
    partner_contact_residues,
)


def _atom(serial: int, name: str, chain: str, residue: int, x: float, y: float, z: float) -> str:
    return (
        f"ATOM  {serial:>5d}  {name:<3s} ALA {chain}{residue:>4d}    "
        f"{x:>8.3f}{y:>8.3f}{z:>8.3f}  1.00  0.00           {name[0]}  "
    )


def _complex() -> str:
    """Target chain U at x = 0, partner chain H advancing away from it.

    Residue 1 is 3 A from the partner and in contact, 2 is 4.5 A and in
    contact, 3 is 9 A away and is not.
    """

    lines = [
        _atom(1, "CA", "U", 1, 0.0, 0.0, 0.0),
        _atom(2, "CA", "U", 2, 0.0, 0.0, 10.0),
        _atom(3, "CA", "U", 3, 0.0, 0.0, 20.0),
        _atom(4, "CA", "H", 50, 3.0, 0.0, 0.0),
        _atom(5, "CA", "H", 51, 4.5, 0.0, 10.0),
        _atom(6, "CA", "H", 52, 9.0, 0.0, 20.0),
    ]
    return "\n".join(lines) + "\nEND\n"


def _blocks() -> list[dict[str, object]]:
    return [
        {"id": "A", "chain": "A", "n": 2, "residues": ["A1", "A2"]},
        {"id": "B", "chain": "A", "n": 1, "residues": ["A3"]},
        {"id": "C", "chain": "A", "n": 2, "residues": ["A1", "A3"]},
    ]


def test_contact_is_heavy_atom_within_five_angstrom() -> None:
    found = partner_contact_residues(_complex(), "U", ("H",))

    assert found["contact_residues"] == [1, 2]
    assert found["cutoff_angstrom"] == 5.0
    assert found["per_partner"] == {"H": [1, 2]}
    assert found["target_residues"] == [1, 2, 3]


def test_a_hydrogen_does_not_make_a_contact() -> None:
    """Hydrogens are modelled inconsistently and would move every count."""

    close = _complex() + _atom(7, "HB1", "H", 53, 0.5, 0.0, 20.0) + "\n"

    assert partner_contact_residues(close, "U", ("H",))["contact_residues"] == [1, 2]


def test_a_chain_that_is_neither_target_nor_partner_is_ignored() -> None:
    """The atlas's CD3e record holds CD3d and its GPC3 record a third binder."""

    with_extra = _complex() + _atom(7, "CA", "Z", 60, 1.0, 0.0, 20.0) + "\n"

    assert partner_contact_residues(with_extra, "U", ("H",))["contact_residues"] == [1, 2]


def test_a_measurement_without_a_declared_partner_is_refused() -> None:
    for chains in ((), ("H", "H"), ("U",)):
        with pytest.raises(InterfaceAreaError):
            partner_contact_residues(_complex(), "U", chains)
    with pytest.raises(InterfaceAreaError):
        partner_contact_residues(_complex(), "Q", ("H",))
    with pytest.raises(InterfaceAreaError):
        partner_contact_residues(_complex(), "U", ("Q",))


def test_the_verdict_vocabulary_is_the_atlas_s_four_words() -> None:
    assert set(VERDICTS) == {"ORTHOGONAL", "ADJACENT", "OVERLAPPING", "ON-TARGET"}
    assert block_verdict(0, "avoid") == "ORTHOGONAL"
    assert block_verdict(EDGE_CONTACT_PCT - 1, "avoid") == "ADJACENT"
    assert block_verdict(100, "avoid") == "OVERLAPPING"
    # ON-TARGET is the direction's word: it appears on both of the atlas's
    # contact_good targets and on none of the other seven.
    assert block_verdict(100, "compete") == "ON-TARGET"
    assert block_verdict(0, "compete") == "ORTHOGONAL"


def test_the_direction_does_not_change_the_measurement_only_its_reading() -> None:
    contact = partner_contact_residues(_complex(), "U", ("H",))

    avoid = classify_blocks(_blocks(), contact, "avoid")
    compete = classify_blocks(_blocks(), contact, "compete")

    assert [(block["contact_n"], block["contact_pct"]) for block in avoid] == [
        (2, 100),
        (0, 0),
        (1, 50),
    ]
    assert [block["contact_n"] for block in compete] == [2, 0, 1]
    assert [block["verdict"] for block in avoid] == ["OVERLAPPING", "ORTHOGONAL", "OVERLAPPING"]
    assert [block["verdict"] for block in compete] == ["ON-TARGET", "ORTHOGONAL", "ON-TARGET"]
    assert "Competes with the partner" in avoid[0]["reading"]
    assert "what this campaign declared it wants" in compete[0]["reading"]
    assert "what this campaign declared it wants" in avoid[1]["reading"]


def test_an_undeclared_direction_still_gets_the_measurement() -> None:
    """The front end does not collect the direction yet, and inferring it is forbidden."""

    classified = classify_blocks(_blocks(), partner_contact_residues(_complex(), "U", ("H",)))

    assert classified[0]["contact_pct"] == 100
    assert classified[0]["verdict"] == "OVERLAPPING"
    assert "not declared" in classified[0]["reading"]
    assert classified[0]["partner_direction"] is None


def test_an_unknown_direction_is_refused_rather_than_read_as_one_of_the_two() -> None:
    with pytest.raises(InterfaceAreaError):
        classify_blocks(_blocks(), partner_contact_residues(_complex(), "U", ("H",)), "maybe")


def test_the_denominator_is_the_block_s_own_residues_not_its_span() -> None:
    """Block C spans residues 1 to 3 and declares two of them.

    The atlas's blocks are contiguous ranges; these survived hotspot
    validation, so counting the span would count a residue the plan never
    declared.
    """

    classified = classify_blocks(_blocks(), partner_contact_residues(_complex(), "U", ("H",)), "avoid")

    assert classified[2]["contact_n"] == 1
    assert classified[2]["contact_pct"] == 50


def test_blocks_join_the_measurement_by_residue_number() -> None:
    """Extracting a chain relabels it: the prepared file is A, the source was U."""

    classified = classify_blocks(
        [{"id": "A", "chain": "A", "n": 1, "residues": ["A1"]}],
        partner_contact_residues(_complex(), "U", ("H",)),
        "avoid",
    )

    assert classified[0]["contact_n"] == 1


def test_a_block_naming_no_residue_number_is_reported_not_counted() -> None:
    classified = classify_blocks(
        [{"id": "A", "chain": "A", "n": 0, "residues": []}],
        partner_contact_residues(_complex(), "U", ("H",)),
        "avoid",
    )

    assert classified[0]["contact_status"] == "block_names_no_residue_number"
    assert "contact_pct" not in classified[0]


def test_an_alternate_location_is_not_two_atoms() -> None:
    line = _atom(1, "CA", "U", 1, 0.0, 0.0, 0.0)
    doubled = line + "\n" + line[:16] + "B" + line[17:]

    assert len(heavy_atoms(doubled)) == 1


def test_a_bridging_water_is_not_a_contact() -> None:
    """Two HOH residues put the m971 footprint on 7O52 at 28 where the atlas says 26."""

    wet = _complex() + _atom(7, "O", "H", 801, 1.0, 0.0, 20.0).replace(" ALA ", " HOH ") + "\n"

    assert partner_contact_residues(wet, "U", ("H",))["contact_residues"] == [1, 2]
    assert 801 not in partner_contact_residues(wet, "H", ("U",))["target_residues"]


def test_the_suggestion_s_blocks_carry_the_verdict_when_the_partner_is_declared() -> None:
    """The validator is where a block is built, so this is where contact reaches it."""

    from cellertz.application.plan_configuration_suggestion import _validated_blocks

    partner = {
        "status": "measured",
        "declared": {"target_chain": "U", "partner_chains": "H", "partner_direction": "avoid"},
        "partner_chains": ["H"],
        "cutoff_angstrom": 5.0,
        **partner_contact_residues(_complex(), "U", ("H",)),
    }
    system: list[str] = []
    raw = [{"id": "A", "residues": ["A1", "A2"], "priority": 1, "selectivity": "HIGH"}]

    blocks = _validated_blocks(raw, ["A1", "A2"], system, partner)

    assert blocks[0]["verdict"] == "OVERLAPPING"
    assert blocks[0]["contact_pct"] == 100
    assert any("Block A is OVERLAPPING: 2 of 2 residues touch chain H at 5.0 A" in note for note in system)


def test_without_a_measurement_a_block_is_built_exactly_as_before() -> None:
    from cellertz.application.plan_configuration_suggestion import _validated_blocks

    raw = [{"id": "A", "residues": ["A1"], "priority": 1, "selectivity": "HIGH"}]

    for partner in (None, {"status": "complex_not_registered", "limitation": "x"}):
        blocks = _validated_blocks(raw, ["A1"], [], partner)
        assert "verdict" not in blocks[0]
        assert "contact_pct" not in blocks[0]
