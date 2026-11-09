from cellertz.evaluation.baselines.structural import (
    percentile_composite_scores,
    rank_by_composite,
    rank_by_feature,
)
from cellertz.schemas import ProteinCandidate


def test_rank_higher_is_better():
    low = ProteinCandidate(
        id="low",
        sequence="AAA",
        design_type="protein",
        interface_metrics={"ipsae": 0.2},
    )

    high = ProteinCandidate(
        id="high",
        sequence="AAA",
        design_type="protein",
        interface_metrics={"ipsae": 0.8},
    )

    ranked = rank_by_feature([low, high], "ipsae")

    assert ranked[0].id == "high"


def test_rank_lower_is_better():
    low_pae = ProteinCandidate(
        id="good",
        sequence="AAA",
        design_type="protein",
        interface_metrics={"pae_interaction": 2.0},
    )

    high_pae = ProteinCandidate(
        id="bad",
        sequence="AAA",
        design_type="protein",
        interface_metrics={"pae_interaction": 10.0},
    )

    ranked = rank_by_feature(
        [high_pae, low_pae],
        "pae_interaction",
    )

    assert ranked[0].id == "good"


def test_missing_feature_goes_last():
    missing = ProteinCandidate(
        id="missing",
        sequence="AAA",
        design_type="protein",
        interface_metrics={"ipsae": None},
    )

    valid = ProteinCandidate(
        id="valid",
        sequence="AAA",
        design_type="protein",
        interface_metrics={"ipsae": 0.5},
    )

    ranked = rank_by_feature([missing, valid], "ipsae")

    assert ranked[-1].id == "missing"


def test_composite_score_prefers_stronger_candidate():
    weak = ProteinCandidate(
        id="weak",
        sequence="AAA",
        design_type="protein",
        interface_metrics={
            "ipsae": 0.2,
            "confidence": 0.3,
        },
    )

    strong = ProteinCandidate(
        id="strong",
        sequence="AAA",
        design_type="protein",
        interface_metrics={
            "ipsae": 0.8,
            "confidence": 0.9,
        },
    )

    ranked = rank_by_composite(
        [weak, strong],
        ["ipsae", "confidence"],
    )

    assert ranked[0].id == "strong"


def test_composite_scores_are_between_zero_and_one():
    candidates = [
        ProteinCandidate(
            id="a",
            sequence="AAA",
            design_type="protein",
            interface_metrics={
                "ipsae": 0.2,
                "confidence": 0.7,
            },
        ),
        ProteinCandidate(
            id="b",
            sequence="AAA",
            design_type="protein",
            interface_metrics={
                "ipsae": 0.8,
                "confidence": 0.9,
            },
        ),
    ]

    scores = percentile_composite_scores(
        candidates,
        ["ipsae", "confidence"],
    )

    assert all(
        0.0 <= score <= 1.0
        for score in scores.values()
    )
