"""The limits the atlas ran its hotspot blocks through.

Its footer records the verdicts as ``hotspot_guard.py`` at ``max_block 6``,
``max_total 40``, and neither number existed anywhere in this codebase. The
guard *marks*: the atlas's own finding across seven campaigns is that a
declared block does not steer the optimiser, so a block is a claim to be
audited after the run rather than a specification to enforce before it.
Dropping an over-limit block would throw away the claim instead of recording
that it was too wide.
"""

from __future__ import annotations

from cellertz.application.plan_configuration_suggestion import (
    MAX_BLOCK_RESIDUES,
    MAX_TOTAL_RESIDUES,
    _validated_blocks,
    hotspot_guard,
)


def _residues(chain: str, numbers: range | list[int]) -> list[str]:
    return [f"{chain}{number}" for number in numbers]


def _blocks(raw: list[dict], kept: list[str]) -> tuple[list[dict], list[str]]:
    system: list[str] = []
    return _validated_blocks(raw, kept, system), system


def test_the_atlas_limits_are_the_ones_recorded() -> None:
    assert MAX_BLOCK_RESIDUES == 6
    assert MAX_TOTAL_RESIDUES == 40


def test_a_block_within_the_limit_carries_a_clean_verdict() -> None:
    kept = _residues("A", range(624, 630))
    blocks, system = _blocks([{"id": "A", "residues": kept}], kept)

    assert len(blocks) == 1
    assert blocks[0]["n"] == 6
    assert blocks[0]["over_block_limit"] is False
    assert not [note for note in system if "max_block" in note or "max_total" in note]
    assert hotspot_guard(blocks)["over_block_limit"] == []


def test_a_block_over_max_block_is_marked_and_kept() -> None:
    """Dropping it would discard the claim instead of recording its width."""

    kept = _residues("A", range(624, 631))
    blocks, system = _blocks([{"id": "A", "residues": kept}], kept)

    assert len(blocks) == 1, "the block survives"
    assert blocks[0]["n"] == 7
    assert blocks[0]["over_block_limit"] is True
    assert any("max_block" in note for note in system)
    assert any("not dropped" in note for note in system)
    assert hotspot_guard(blocks)["over_block_limit"] == ["A"]


def test_the_total_budget_is_a_verdict_on_the_set() -> None:
    """41 residues over seven blocks: no single block is to blame."""

    kept = _residues("A", range(600, 641))
    raw = [
        {"id": chr(65 + index), "residues": kept[index * 6 : index * 6 + 6]}
        for index in range(7)
    ]
    blocks, system = _blocks(raw, kept)
    guard = hotspot_guard(blocks)

    assert guard["total_residues"] == 41
    assert guard["over_total_limit"] is True
    assert guard["over_block_limit"] == [], "every block is within max_block"
    assert any("max_total" in note for note in system)


def test_a_set_at_exactly_the_budget_is_not_over() -> None:
    kept = _residues("A", range(600, 640))
    raw = [
        {"id": chr(65 + index), "residues": kept[index * 5 : index * 5 + 5]}
        for index in range(8)
    ]
    blocks, _system = _blocks(raw, kept)
    guard = hotspot_guard(blocks)

    assert guard["total_residues"] == 40
    assert guard["over_total_limit"] is False


def test_the_verdict_counts_only_residues_that_survived_validation() -> None:
    """A block cannot blow the budget with residues the structure refused."""

    kept = _residues("A", range(624, 628))
    raw = [{"id": "A", "residues": kept + _residues("A", range(700, 760))}]
    blocks, system = _blocks(raw, kept)
    guard = hotspot_guard(blocks)

    assert blocks[0]["n"] == 4
    assert guard["total_residues"] == 4
    assert guard["over_total_limit"] is False
    assert guard["over_block_limit"] == []
    assert any("not an accepted hotspot" in note for note in system)


def test_the_guard_reports_its_own_limits_so_a_reply_carries_them() -> None:
    """A verdict without its threshold cannot be re-checked later."""

    guard = hotspot_guard([])

    assert guard["max_block"] == MAX_BLOCK_RESIDUES
    assert guard["max_total"] == MAX_TOTAL_RESIDUES
    assert guard["total_residues"] == 0
    assert guard["over_total_limit"] is False
