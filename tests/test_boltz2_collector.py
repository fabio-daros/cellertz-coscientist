import json

import pytest

from cellertz.tools.protein_scoring.boltz2_collector import (
    collect_prediction_directory,
    extract_candidate_id,
)


def _write_confidence(
    path,
    iptm,
    ptm=0.90,
):
    path.write_text(
        json.dumps(
            {
                "confidence_score": 0.95,
                "ptm": ptm,
                "iptm": iptm,
                "protein_iptm": iptm,
                "complex_plddt": 0.96,
                "complex_iplddt": 0.97,
            }
        )
    )


def test_extract_candidate_id():
    stem = (
        "negative_high_ipsae__"
        "BCMA_candidate_123"
    )

    assert extract_candidate_id(stem) == (
        "BCMA_candidate_123"
    )


def test_extract_candidate_id_without_role_prefix():
    assert extract_candidate_id(
        "BCMA_l60_s542430_mpnn3"
    ) == "BCMA_l60_s542430_mpnn3"


def test_collect_real_boltz_layout(
    tmp_path,
):
    prediction_dir = (
        tmp_path
        / "pilot_role__candidate_123"
    )
    prediction_dir.mkdir()

    values = [
        0.91,
        0.93,
        0.89,
        0.92,
        0.90,
    ]

    for index, value in enumerate(values):
        _write_confidence(
            prediction_dir
            / (
                "confidence_pilot_role__"
                f"candidate_123_model_{index}.json"
            ),
            iptm=value,
        )

    result = collect_prediction_directory(
        prediction_dir,
        expected_samples=5,
    )

    assert result.candidate_id == "candidate_123"
    assert result.summary.n_predictions == 5
    assert result.summary.iptm_mean == pytest.approx(
        0.91
    )
    assert result.summary.iptm_median == pytest.approx(
        0.91
    )
    assert result.summary.iptm_min == pytest.approx(
        0.89
    )
    assert result.summary.iptm_max == pytest.approx(
        0.93
    )


def test_collector_orders_models_numerically(
    tmp_path,
):
    prediction_dir = (
        tmp_path
        / "role__candidate"
    )
    prediction_dir.mkdir()

    for index in [4, 0, 3, 1, 2]:
        _write_confidence(
            prediction_dir
            / (
                "confidence_role__candidate_"
                f"model_{index}.json"
            ),
            iptm=0.90 + index / 100,
        )

    result = collect_prediction_directory(
        prediction_dir,
        expected_samples=5,
    )

    assert [
        path.name.rsplit("_model_", 1)[1]
        for path in result.sample_paths
    ] == [
        "0.json",
        "1.json",
        "2.json",
        "3.json",
        "4.json",
    ]
    assert result.summary.iptm_model_0 == pytest.approx(0.90)


def test_wrong_sample_count_is_rejected(
    tmp_path,
):
    prediction_dir = (
        tmp_path
        / "role__candidate"
    )
    prediction_dir.mkdir()

    for index in range(4):
        _write_confidence(
            prediction_dir
            / (
                "confidence_role__candidate_"
                f"model_{index}.json"
            ),
            iptm=0.90,
        )

    with pytest.raises(ValueError):
        collect_prediction_directory(
            prediction_dir,
            expected_samples=5,
        )
