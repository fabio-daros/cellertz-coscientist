from cellertz.evaluation.datasets.bcma_features import BCMA_FEATURES_V1
from cellertz.schemas import ProteinCandidate
from cellertz.tools.protein_scoring import (
    BENCHMARK_VERSION,
    EVIDENCE_FEATURES,
    ProteinScoringTool,
)


def _candidate(
    candidate_id: str,
    interface_metrics: dict | None = None,
) -> ProteinCandidate:
    return ProteinCandidate(
        id=candidate_id,
        sequence="AAA",
        design_type="protein",
        source="Chow et al. 2026",
        target="BCMA",
        metadata={"campaign": "BCMA BindCraft Campaign 1"},
        interface_metrics=interface_metrics or {},
    )


def test_feature_contract_is_frozen():
    assert len(BCMA_FEATURES_V1) == 57


def test_score_uses_raw_ipsae_and_is_not_a_probability():
    tool = ProteinScoringTool()

    result = tool.score(
        _candidate(
            "binder-001",
            {
                "ipsae": 0.887,
                "iptm": 0.931,
                "complex_plddt": 0.952,
                "pae_interaction": 2.41,
                "confidence": 0.946,
            },
        )
    )

    assert result.candidate_id == "binder-001"
    assert result.ranking_score == 0.887
    assert result.ranking_method == "ipsae"
    assert result.is_probability is False
    assert result.recommended_as_hard_filter is False
    assert result.benchmark_version == BENCHMARK_VERSION
    assert result.evidence["ipsae"] == 0.887
    assert result.evidence["iptm"] == 0.931
    assert set(result.evidence) == set(EVIDENCE_FEATURES)
    assert result.warnings == ()
    assert "not a binding probability" in " ".join(
        result.limitations
    )


def test_missing_ipsae_has_no_ranking_score():
    tool = ProteinScoringTool()

    result = tool.score(
        _candidate(
            "missing",
            {"iptm": 0.90, "confidence": 0.80},
        )
    )

    assert result.ranking_score is None
    assert result.evidence["ipsae"] is None
    assert result.evidence["iptm"] == 0.90
    assert result.is_probability is False
    assert result.recommended_as_hard_filter is False
    assert any("Missing ipSAE" in warning for warning in result.warnings)


def test_rank_orders_by_ipsae_and_puts_missing_last():
    tool = ProteinScoringTool()

    low = _candidate("low", {"ipsae": 0.20})
    high = _candidate("high", {"ipsae": 0.90})
    missing = _candidate("missing", {"iptm": 0.99})

    ranked = tool.rank([low, missing, high])

    assert [result.candidate_id for result in ranked] == [
        "high",
        "low",
        "missing",
    ]
    assert ranked[-1].ranking_score is None
