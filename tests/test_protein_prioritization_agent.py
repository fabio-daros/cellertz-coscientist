from cellertz.domains.protein_design.agents.critic import (
    Agreement,
    CriticAssessment,
    ProteinCriticRecord,
)
from cellertz.domains.protein_design.agents.prioritization import (
    PortfolioPolicy,
    ProteinPrioritizationAgentV1,
)


def _reviewed(
    candidate_id: str,
    rank: int,
    ranking_score: float | None,
    *,
    agreement: Agreement = "high",
    structural_conflict: bool | None = False,
    missing_evidence: bool = False,
) -> ProteinCriticRecord:
    return ProteinCriticRecord(
        candidate_id=candidate_id,
        rank=rank,
        ranking_signal="ipsae",
        ranking_score=ranking_score,
        is_probability=False,
        recommended_as_hard_filter=False,
        structural_evidence={"ipsae": ranking_score},
        critic_assessment=CriticAssessment(
            agreement=agreement,
            ensemble_consistency="unknown",
            structural_conflict=structural_conflict,
            missing_evidence=missing_evidence,
            confidence_note="test",
        ),
    )


def test_prioritization_never_changes_rank_or_score():
    reviewed = [
        _reviewed("A", 1, 0.90, agreement="low", structural_conflict=True),
        _reviewed("B", 2, 0.80),
        _reviewed("C", 3, 0.70),
    ]
    policy = PortfolioPolicy(
        core_slots=1,
        novelty_slots=0,
        exploratory_slots=0,
    )
    selected = ProteinPrioritizationAgentV1(policy).select(reviewed)

    for source, output in zip(reviewed, selected, strict=True):
        assert output.rank == source.rank
        assert output.ranking_score == source.ranking_score
        assert output.candidate_id == source.candidate_id


def test_low_agreement_does_not_exclude_core():
    reviewed = [
        _reviewed(
            "A",
            1,
            0.90,
            agreement="low",
            structural_conflict=True,
        ),
        _reviewed("B", 2, 0.80),
    ]
    policy = PortfolioPolicy(
        core_slots=1,
        novelty_slots=0,
        exploratory_slots=0,
    )
    selected = ProteinPrioritizationAgentV1(policy).select(reviewed)

    assert selected[0].candidate_id == "A"
    assert selected[0].selected is True
    assert selected[0].selection_bucket == "core"
    assert selected[0].rank == 1
    assert selected[0].review_flag is True
    assert "not excluded" in selected[0].selection_reason


def test_portfolio_keeps_original_ranks():
    reviewed = [
        _reviewed(f"c{index}", index, 1.0 - index / 100)
        for index in range(1, 12)
    ]
    reviewed.append(
        _reviewed(
            "tail",
            41,
            0.10,
            agreement="unknown",
            missing_evidence=True,
        )
    )
    clusters = {
        record.candidate_id: (
            "core_family"
            if record.rank <= 10
            else "tail_family"
        )
        for record in reviewed
    }
    policy = PortfolioPolicy(
        core_slots=6,
        novelty_slots=1,
        exploratory_slots=1,
    )
    selected = ProteinPrioritizationAgentV1(policy).select(
        reviewed,
        clusters=clusters,
    )
    by_id = {
        record.candidate_id: record
        for record in selected
    }

    assert [record.rank for record in selected[:11]] == list(
        range(1, 12)
    )
    assert by_id["c1"].selection_bucket == "core"
    assert by_id["c6"].selection_bucket == "core"
    assert by_id["c7"].selected is False
    assert by_id["c11"].selection_bucket == "novel"
    assert by_id["c11"].rank == 11
    assert by_id["tail"].rank == 41
    assert by_id["tail"].selected is True
    assert by_id["tail"].selection_bucket == "exploratory"


def test_novel_skips_clusters_already_in_core():
    reviewed = [
        _reviewed("A", 1, 0.90),
        _reviewed("B", 2, 0.80),
        _reviewed("C", 3, 0.70),
    ]
    clusters = {"A": "fam1", "B": "fam1", "C": "fam2"}
    policy = PortfolioPolicy(
        core_slots=1,
        novelty_slots=1,
        exploratory_slots=0,
    )
    selected = ProteinPrioritizationAgentV1(policy).select(
        reviewed,
        clusters=clusters,
    )

    assert selected[0].selection_bucket == "core"
    assert selected[1].selected is False
    assert selected[2].selection_bucket == "novel"
    assert selected[2].rank == 3


def test_critic_is_not_a_hard_filter():
    reviewed = [
        _reviewed("keep_conflict", 1, 0.90, agreement="low"),
        _reviewed("plain", 2, 0.50, agreement="high"),
    ]
    policy = PortfolioPolicy(
        core_slots=2,
        novelty_slots=0,
        exploratory_slots=0,
    )
    selected = ProteinPrioritizationAgentV1(policy).select(reviewed)

    assert [record.selected for record in selected] == [
        True,
        True,
    ]
    assert selected[0].selection_bucket == "core"
    assert selected[1].selection_bucket == "core"
    assert selected[0].is_probability is False
    assert "priority_score" not in selected[0].to_dict()
