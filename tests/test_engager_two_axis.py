"""The professor's two-axis ranking.

The property that matters is the one his data forced: a design rises only by
being good on both axes, because the axes are nearly independent and a blended
sum let a design ride one of them.
"""

from __future__ import annotations

import pytest

from cellertz.domains.protein_design.scoring.engager_two_axis import (
    I_PTM_GATE,
    PROTOCOL_ID,
    WEIGHTS,
    EngagerDesign,
    EngagerRankingError,
    count_liabilities,
    epitope_jaccard,
    rank_engager_designs,
    sequence_identity,
)

CDRS = ((26, 35), (49, 65), (96, 109))
CLINICAL = (frozenset({624, 625, 626, 627, 628}),)


def design(
    design_id: str,
    *,
    trajectory: str | None = None,
    i_ptm: float = 0.9,
    epitope: set[int] | None = None,
    buried_area: float = 800.0,
    net_charge: float = 0.0,
    sequence: str = "A" * 120,
) -> EngagerDesign:
    return EngagerDesign(
        design_id=design_id,
        trajectory=trajectory or f"traj_{design_id}",
        sequence=sequence,
        i_ptm=i_ptm,
        epitope=frozenset(epitope or {624, 625, 626}),
        buried_area=buried_area,
        net_charge=net_charge,
    )


def ranked(designs, select=None):
    return rank_engager_designs(designs, CLINICAL, CDRS, select=select)


def by_id(ranking):
    return {item.design_id: item for item in ranking.designs}


def test_a_design_below_the_iptm_gate_is_rejected_and_named() -> None:
    ranking = ranked([design("keep", i_ptm=0.81), design("drop", i_ptm=I_PTM_GATE - 0.01)])
    assert ranking.rejected_by_gate == ("drop",)
    assert [item.design_id for item in ranking.designs] == ["keep"]


def test_the_score_is_the_worse_axis_so_one_good_axis_does_not_carry_a_design() -> None:
    """The whole reason for two axes. A design that is best on i_pTM and worst
    on quality must not outrank one that is middling on both."""

    cohort = [
        # Best i_pTM, worst composite: high charge and a tiny epitope overlap.
        design("rides_iptm", i_ptm=0.99, net_charge=9.0, buried_area=10.0, epitope={999}),
        design("middling", i_ptm=0.90, net_charge=1.0, buried_area=500.0, epitope={624, 625}),
        design("rides_quality", i_ptm=0.81, net_charge=-2.0, buried_area=900.0, epitope={624, 625, 626, 627, 628}),
    ]
    ranking = ranked(cohort)
    rows = by_id(ranking)
    assert rows["rides_iptm"].i_ptm_percentile == 1.0
    assert rows["rides_iptm"].score < rows["middling"].score
    assert rows["rides_quality"].score < rows["middling"].score
    assert ranking.designs[0].design_id == "middling"


def test_every_score_carries_the_protocol_that_produced_it() -> None:
    document = ranked([design("a"), design("b", i_ptm=0.95)]).to_dict()
    assert document["protocol_id"] == PROTOCOL_ID
    assert document["weights"] == WEIGHTS
    assert "CARPNN_2026_REFERENCE" in document["not_comparable_with"]
    assert all(row["protocol_id"] == PROTOCOL_ID for row in document["designs"])
    assert document["designs"][0]["score_rule"] == "the worse of the two percentiles"


def test_the_normalisation_is_declared_as_our_reading_not_his_method() -> None:
    ranking = ranked([design("a"), design("b", i_ptm=0.95)])
    assert "percentile" in ranking.normalisation
    assert any("not a stated step" in note for note in ranking.interpretations)


def test_the_cohort_is_refused_without_the_clinical_epitope_the_formula_weights() -> None:
    with pytest.raises(EngagerRankingError) as error:
        rank_engager_designs([design("a")], (), CDRS)
    assert str(error.value) == "clinical_epitope_not_declared"


def test_the_pareto_front_holds_what_nothing_else_beats_on_both_axes() -> None:
    """A trade-off keeps three designs on the front; only one is beaten on both.

    ``beaten`` is below ``middle`` on i_pTM and on quality at once, which is
    the only way to leave the front when the two axes disagree.
    """

    cohort = [
        design("high_iptm", i_ptm=0.99, buried_area=100.0, net_charge=9.0),
        design("middle", i_ptm=0.95, buried_area=700.0, net_charge=-1.0),
        design("beaten", i_ptm=0.90, buried_area=500.0, net_charge=1.0),
        design("high_quality", i_ptm=0.81, buried_area=900.0, net_charge=-3.0),
    ]
    rows = by_id(ranked(cohort))
    assert rows["high_iptm"].on_pareto_front is True
    assert rows["high_quality"].on_pareto_front is True
    assert rows["middle"].on_pareto_front is True
    assert rows["beaten"].on_pareto_front is False


def test_two_designs_from_one_trajectory_cannot_both_be_selected() -> None:
    cohort = [
        design("first", trajectory="traj_1", i_ptm=0.99, sequence="A" * 120),
        design("second", trajectory="traj_1", i_ptm=0.98, sequence="C" * 120),
        design("other", trajectory="traj_2", i_ptm=0.97, sequence="D" * 120),
    ]
    rows = by_id(ranked(cohort))
    assert [item for item in ("first", "second") if rows[item].selected] == ["first"]
    assert rows["other"].selected is True


def test_a_near_duplicate_needs_both_epitope_and_sequence_to_be_dropped() -> None:
    twin = "M" + "A" * 119
    cohort = [
        design("original", trajectory="t1", i_ptm=0.99, sequence="A" * 120, epitope={624, 625, 626}),
        # Same epitope, same sequence: a repeat.
        design("repeat", trajectory="t2", i_ptm=0.98, sequence="A" * 120, epitope={624, 625, 626}),
        # Same epitope, a different sequence: a finding, not a repeat.
        design("other_route", trajectory="t3", i_ptm=0.97, sequence="K" * 120, epitope={624, 625, 626}),
        # Nearly the same sequence, a different epitope: also kept.
        design("other_site", trajectory="t4", i_ptm=0.96, sequence=twin, epitope={700, 701, 702}),
    ]
    rows = by_id(ranked(cohort))
    assert rows["original"].selected is True
    assert rows["repeat"].selected is False
    assert rows["other_route"].selected is True
    assert rows["other_site"].selected is True


def test_selection_stops_at_the_requested_count() -> None:
    cohort = [
        design(f"d{index}", i_ptm=0.9 + index / 100, sequence=residue * 120)
        for index, residue in enumerate("ACDEFG")
    ]
    ranking = ranked(cohort, select=2)
    assert sum(1 for item in ranking.designs if item.selected) == 2


def test_liabilities_count_the_three_the_atlas_weights_separately() -> None:
    # NG and NS deamidate; DG and DS isomerise; M and W count inside a CDR only.
    sequence = list("A" * 120)
    sequence[0:2] = "NG"
    sequence[2:4] = "DS"
    sequence[25] = "M"  # inside CDR1 (26-35, 1-based)
    sequence[40] = "W"  # framework, must not count
    counted = count_liabilities("".join(sequence), CDRS)
    assert counted == {"deamidation": 1, "isomerisation": 1, "met_trp_in_cdr": 1}


def test_an_invalid_cdr_span_is_refused_rather_than_counted_as_empty() -> None:
    with pytest.raises(EngagerRankingError) as error:
        count_liabilities("AAAA", ((0, 2),))
    assert str(error.value) == "invalid_cdr_span"


def test_jaccard_and_identity_are_the_plain_definitions() -> None:
    assert epitope_jaccard(frozenset({1, 2}), frozenset({1, 2})) == 1.0
    assert epitope_jaccard(frozenset({1, 2}), frozenset({2, 3})) == pytest.approx(1 / 3)
    assert epitope_jaccard(frozenset(), frozenset()) == 0.0
    assert sequence_identity("AAAA", "AAAA") == 1.0
    assert sequence_identity("AAAA", "AAAAAAAA") == 0.5
    assert sequence_identity("", "") == 0.0


def test_ties_share_a_percentile_so_arrival_order_cannot_separate_them() -> None:
    cohort = [design("a", i_ptm=0.9), design("b", i_ptm=0.9), design("c", i_ptm=0.9)]
    rows = by_id(ranked(cohort))
    assert len({row.i_ptm_percentile for row in rows.values()}) == 1


def test_a_duplicate_design_id_is_refused() -> None:
    with pytest.raises(EngagerRankingError) as error:
        ranked([design("a"), design("a")])
    assert str(error.value) == "duplicate_design_id"


def test_a_cohort_entirely_below_the_gate_ranks_nothing_rather_than_ranking_badly() -> None:
    ranking = ranked([design("a", i_ptm=0.5), design("b", i_ptm=0.7)])
    assert ranking.designs == ()
    assert set(ranking.rejected_by_gate) == {"a", "b"}
