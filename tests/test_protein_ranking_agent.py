import pytest

from cellertz.domains.protein_design.agents.ranking import ProteinRankingAgentV1
from cellertz.schemas import ProteinCandidate
from cellertz.tools.protein_scoring.boltz2_ensemble import (
    Boltz2Sample,
    summarize_boltz2_ensemble,
)
from cellertz.tools.protein_scoring.scoring_v2 import (
    ProteinScoringToolV2,
)


def _candidate(
    candidate_id: str,
    ipsae: float | None,
) -> ProteinCandidate:
    interface_metrics = {}

    if ipsae is not None:
        interface_metrics["ipsae"] = ipsae

    return ProteinCandidate(
        id=candidate_id,
        sequence="AAA",
        design_type="protein",
        source="test",
        target="BCMA",
        interface_metrics=interface_metrics,
    )


def _ensemble(model_0_iptm: float):
    return summarize_boltz2_ensemble(
        [
            Boltz2Sample(
                sample_id="confidence_x_model_0",
                iptm=model_0_iptm,
                model_index=0,
            ),
            Boltz2Sample(
                sample_id="confidence_x_model_1",
                iptm=model_0_iptm + 0.01,
                model_index=1,
            ),
        ]
    )


def test_ranking_ignores_boltz2_evidence():
    agent = ProteinRankingAgentV1()

    ranked = agent.rank_candidates(
        [
            _candidate("B", 0.80),
            _candidate("A", 0.90),
        ],
        {
            "A": _ensemble(0.20),
            "B": _ensemble(0.99),
        },
    )

    assert [record.candidate_id for record in ranked] == [
        "A",
        "B",
    ]
    assert [record.rank for record in ranked] == [1, 2]
    assert ranked[0].ranking_score == 0.90
    assert ranked[1].ranking_score == 0.80
    assert ranked[0].ranking_signal == "ipsae"
    assert ranked[0].is_probability is False
    assert ranked[0].recommended_as_hard_filter is False
    assert ranked[0].structural_evidence["boltz2_iptm_model_0"] == 0.20
    assert ranked[1].structural_evidence["boltz2_iptm_model_0"] == 0.99


def test_equal_ipsae_breaks_ties_by_candidate_id():
    agent = ProteinRankingAgentV1()

    ranked = agent.rank_candidates(
        [
            _candidate("B", 0.90),
            _candidate("A", 0.90),
        ]
    )

    assert [record.candidate_id for record in ranked] == [
        "A",
        "B",
    ]
    assert ranked[0].ranking_score == 0.90
    assert ranked[1].ranking_score == 0.90
    assert ranked[0].rank == 1
    assert ranked[1].rank == 2


def test_missing_ipsae_sorts_last_by_candidate_id():
    agent = ProteinRankingAgentV1()

    ranked = agent.rank_candidates(
        [
            _candidate("missing_b", None),
            _candidate("scored", 0.10),
            _candidate("missing_a", None),
        ]
    )

    assert [record.candidate_id for record in ranked] == [
        "scored",
        "missing_a",
        "missing_b",
    ]
    assert ranked[0].ranking_score == 0.10
    assert ranked[1].ranking_score is None
    assert ranked[2].ranking_score is None


def test_ranking_is_independent_of_input_order():
    agent = ProteinRankingAgentV1()
    candidates = [
        _candidate("C", 0.70),
        _candidate("A", 0.90),
        _candidate("B", 0.80),
    ]

    forward = agent.rank_candidates(candidates)
    reverse = agent.rank_candidates(list(reversed(candidates)))

    assert [record.candidate_id for record in forward] == [
        "A",
        "B",
        "C",
    ]
    assert [record.candidate_id for record in reverse] == [
        "A",
        "B",
        "C",
    ]


def test_duplicate_candidate_id_is_rejected():
    tool = ProteinScoringToolV2()
    agent = ProteinRankingAgentV1(scorer=tool)
    result = tool.score(_candidate("dup", 0.50))

    with pytest.raises(ValueError, match="Duplicate candidate_id"):
        agent.rank([result, result])
