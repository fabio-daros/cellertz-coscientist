import pytest

from cellertz.domains.protein_design.agents.critic import ProteinCriticAgentV1
from cellertz.domains.protein_design.agents.ranking import ProteinRankingRecord


def _record(
    candidate_id: str,
    rank: int,
    ranking_score: float | None,
    *,
    boltz_mean: float | None = None,
    model_0: float | None = None,
) -> ProteinRankingRecord:
    return ProteinRankingRecord(
        candidate_id=candidate_id,
        rank=rank,
        ranking_signal="ipsae",
        ranking_score=ranking_score,
        is_probability=False,
        recommended_as_hard_filter=False,
        structural_evidence={
            "ipsae": ranking_score,
            "boltz2_iptm_mean": boltz_mean,
            "boltz2_iptm_model_0": model_0,
        },
    )


def test_critic_never_changes_rank_or_score():
    ranked = [
        _record("A", 1, 0.90, boltz_mean=0.20),
        _record("B", 2, 0.80, boltz_mean=0.99),
    ]
    reviewed = ProteinCriticAgentV1().review(ranked)

    for source, output in zip(ranked, reviewed, strict=True):
        assert output.rank == source.rank
        assert output.ranking_score == source.ranking_score
        assert output.candidate_id == source.candidate_id


def test_high_boltz_does_not_promote_low_ipsae():
    ranked = [
        _record("A", 1, 0.90, boltz_mean=0.20, model_0=0.20),
        _record("B", 2, 0.80, boltz_mean=0.99, model_0=0.99),
    ]
    reviewed = ProteinCriticAgentV1().review(ranked)

    assert [record.candidate_id for record in reviewed] == [
        "A",
        "B",
    ]
    assert [record.rank for record in reviewed] == [1, 2]
    assert reviewed[0].critic_assessment.agreement == "low"
    assert reviewed[1].critic_assessment.agreement == "low"
    assert reviewed[0].critic_assessment.structural_conflict is True
    assert reviewed[0].is_probability is False
    assert not hasattr(reviewed[0], "critic_score")
    payload = reviewed[0].to_dict()
    assert "critic_score" not in payload
    assert payload["is_probability"] is False
    assert payload["structural_evidence"]["boltz2_iptm_mean"] == 0.20


def test_low_boltz_does_not_demote_high_ipsae():
    ranked = [
        _record("A", 1, 0.90, boltz_mean=0.20),
        _record("B", 2, 0.80, boltz_mean=0.99),
    ]
    reviewed = ProteinCriticAgentV1().review(ranked)

    assert reviewed[0].candidate_id == "A"
    assert reviewed[0].rank == 1
    assert reviewed[0].ranking_score == 0.90


def test_aligned_evidence_is_high_agreement():
    ranked = [
        _record("A", 1, 0.91, boltz_mean=0.94),
        _record("B", 2, 0.70, boltz_mean=0.61),
    ]
    reviewed = ProteinCriticAgentV1().review(ranked)

    assert reviewed[0].critic_assessment.agreement == "high"
    assert reviewed[1].critic_assessment.agreement == "high"
    assert reviewed[0].critic_assessment.structural_conflict is False
    assert reviewed[0].rank == 1
    assert reviewed[1].rank == 2


def test_missing_evidence_is_unknown_not_invented():
    ranked = [
        _record("A", 1, 0.90, boltz_mean=0.94),
        _record("B", 2, 0.80),
    ]
    reviewed = ProteinCriticAgentV1().review(ranked)

    assert reviewed[1].structural_evidence["boltz2_iptm_mean"] is None
    assert reviewed[1].critic_assessment.missing_evidence is True
    assert reviewed[1].critic_assessment.agreement == "unknown"
    assert reviewed[1].critic_assessment.structural_conflict is None
    assert reviewed[0].critic_assessment.agreement == "unknown"
    assert reviewed[0].rank == 1
    assert reviewed[1].rank == 2


def test_raw_numbers_remain_and_no_probability_is_created():
    ranked = [
        _record("A", 1, 0.91, boltz_mean=0.94, model_0=0.93),
        _record("B", 2, 0.70, boltz_mean=0.61, model_0=0.60),
    ]
    reviewed = ProteinCriticAgentV1().review(ranked)
    payload = reviewed[0].to_dict()
    assessment = payload["critic_assessment"]

    assert payload["structural_evidence"]["boltz2_iptm_mean"] == 0.94
    assert payload["structural_evidence"]["boltz2_iptm_model_0"] == 0.93
    assert assessment["agreement"] in {
        "high",
        "mixed",
        "low",
        "unknown",
    }
    assert isinstance(assessment["agreement"], str)
    assert reviewed[0].is_probability is False
    assert "quality" not in payload
    assert "priority_score" not in payload
    assert assessment.get("critic_score") is None


def test_mixed_agreement_still_preserves_rank():
    ranked = [
        _record("A", 1, 0.90, boltz_mean=0.99),
        _record("B", 2, 0.80, boltz_mean=0.10),
        _record("C", 3, 0.70, boltz_mean=0.50),
    ]
    reviewed = ProteinCriticAgentV1().review(ranked)

    assert [record.rank for record in reviewed] == [1, 2, 3]
    assert [record.candidate_id for record in reviewed] == [
        "A",
        "B",
        "C",
    ]
    assert reviewed[0].critic_assessment.agreement == "high"
    assert reviewed[1].critic_assessment.agreement == "mixed"
    assert reviewed[2].critic_assessment.agreement == "mixed"


def test_critic_preserves_input_order():
    ranked = [
        _record("B", 2, 0.80, boltz_mean=0.50),
        _record("A", 1, 0.90, boltz_mean=0.90),
    ]
    reviewed = ProteinCriticAgentV1().review(ranked)

    assert [record.candidate_id for record in reviewed] == [
        "B",
        "A",
    ]
    assert reviewed[0].rank == 2
    assert reviewed[1].rank == 1


def test_duplicate_candidate_id_is_rejected():
    record = _record("dup", 1, 0.50, boltz_mean=0.50)

    with pytest.raises(ValueError, match="Duplicate candidate_id"):
        ProteinCriticAgentV1().review([record, record])


def test_agreement_is_qualitative_heuristic_not_probability():
    import cellertz.domains.protein_design.agents.critic as critic_module

    ranked = [
        _record("A", 1, 0.90, boltz_mean=0.20),
        _record("B", 2, 0.80, boltz_mean=0.99),
    ]
    reviewed = ProteinCriticAgentV1().review(ranked)

    assert reviewed[0].is_probability is False
    assert reviewed[0].critic_assessment.agreement in {
        "high",
        "mixed",
        "low",
        "unknown",
    }
    assert "not experimentally calibrated" in critic_module.__doc__
    assert "binding probabilities" in critic_module.__doc__
