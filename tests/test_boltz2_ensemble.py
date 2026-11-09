import json

import pytest

from cellertz.tools.protein_scoring.boltz2_ensemble import (
    Boltz2Sample,
    load_boltz2_sample,
    summarize_boltz2_ensemble,
)


def test_ensemble_summary():
    samples = [
        Boltz2Sample(
            sample_id="a",
            iptm=0.90,
            ptm=0.80,
        ),
        Boltz2Sample(
            sample_id="b",
            iptm=0.92,
            ptm=0.82,
        ),
        Boltz2Sample(
            sample_id="c",
            iptm=0.88,
            ptm=0.84,
        ),
    ]

    summary = summarize_boltz2_ensemble(
        samples
    )

    assert summary.n_predictions == 3
    assert summary.iptm_mean == pytest.approx(0.90)
    assert summary.iptm_median == pytest.approx(0.90)
    assert summary.iptm_min == pytest.approx(0.88)
    assert summary.iptm_max == pytest.approx(0.92)
    assert summary.iptm_std > 0
    assert summary.ptm_mean == pytest.approx(0.82)
    assert summary.iptm_model_0 is None


def test_single_sample_has_zero_std():
    summary = summarize_boltz2_ensemble(
        [
            Boltz2Sample(
                sample_id="single",
                iptm=0.91,
            )
        ]
    )

    assert summary.iptm_std == 0.0


def test_empty_ensemble_is_rejected():
    with pytest.raises(ValueError):
        summarize_boltz2_ensemble([])


def test_load_boltz2_confidence_json(
    tmp_path,
):
    path = tmp_path / "confidence_model_0.json"

    path.write_text(
        json.dumps(
            {
                "confidence_score": 0.91,
                "ptm": 0.88,
                "iptm": 0.93,
                "protein_iptm": 0.93,
                "complex_plddt": 0.90,
                "complex_iplddt": 0.92,
            }
        )
    )

    sample = load_boltz2_sample(path)

    assert sample.iptm == pytest.approx(0.93)
    assert sample.ptm == pytest.approx(0.88)
    assert sample.protein_iptm == pytest.approx(0.93)
    assert sample.structure_confidence == pytest.approx(
        0.91
    )
    assert sample.model_index == 0


def test_model_0_iptm_uses_index_not_list_order():
    samples = [
        Boltz2Sample(
            sample_id="confidence_x_model_3",
            iptm=0.99,
            model_index=3,
        ),
        Boltz2Sample(
            sample_id="confidence_x_model_0",
            iptm=0.41,
            model_index=0,
        ),
        Boltz2Sample(
            sample_id="confidence_x_model_4",
            iptm=0.10,
            model_index=4,
        ),
    ]

    summary = summarize_boltz2_ensemble(samples)

    assert summary.iptm_model_0 == pytest.approx(0.41)
    assert samples[0].iptm != summary.iptm_model_0


def test_missing_model_0_is_not_substituted():
    summary = summarize_boltz2_ensemble(
        [
            Boltz2Sample(
                sample_id="confidence_x_model_1",
                iptm=0.99,
                model_index=1,
            ),
            Boltz2Sample(
                sample_id="confidence_x_model_2",
                iptm=0.80,
                model_index=2,
            ),
        ]
    )

    assert summary.iptm_model_0 is None
    assert summary.iptm_mean == pytest.approx(0.895)


def test_duplicate_model_0_is_rejected():
    with pytest.raises(ValueError, match="model_index 0"):
        summarize_boltz2_ensemble(
            [
                Boltz2Sample(
                    sample_id="a_model_0",
                    iptm=0.40,
                    model_index=0,
                ),
                Boltz2Sample(
                    sample_id="b_model_0",
                    iptm=0.50,
                    model_index=0,
                ),
            ]
        )
