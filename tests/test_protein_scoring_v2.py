import pytest

from cellertz.schemas import ProteinCandidate
from cellertz.tools.protein_scoring.boltz2_ensemble import (
    Boltz2EnsembleSummary,
    Boltz2Sample,
    summarize_boltz2_ensemble,
)
from cellertz.tools.protein_scoring.boltz2_yaml import (
    build_boltz2_complex_yaml,
    write_boltz2_yaml,
)
from cellertz.tools.protein_scoring.scoring_v2 import (
    ADDITIONAL_EVIDENCE_KEYS,
    STRUCTURAL_EVIDENCE_KEYS,
    ProteinScoringToolV2,
)


def _candidate(
    candidate_id: str,
    interface_metrics: dict | None = None,
    sequence: str = "AAA",
    target_sequence: str = "CSQNEYFDSLLHACIPCQLRCSSNTPPLTCQRYCNA",
) -> ProteinCandidate:
    return ProteinCandidate(
        id=candidate_id,
        sequence=sequence,
        design_type="protein",
        source="Chow et al. 2026",
        target="BCMA",
        metadata={
            "campaign": "BCMA BindCraft Campaign 1",
            "target_sequence": target_sequence,
        },
        interface_metrics=interface_metrics or {},
    )


def test_v2_ranking_remains_ipsae_not_ensemble():
    tool = ProteinScoringToolV2()

    ensemble = summarize_boltz2_ensemble(
        [
            Boltz2Sample(sample_id="a", iptm=0.99, ptm=0.80),
            Boltz2Sample(sample_id="b", iptm=0.97, ptm=0.82),
        ]
    )

    result = tool.score(
        _candidate(
            "binder-001",
            {"ipsae": 0.887, "iptm": 0.931},
        ),
        ensemble,
    )

    assert result.baseline_score == 0.887
    assert result.ranking_score == 0.887
    assert result.ranking_score == result.structural_evidence["ipsae"]
    assert result.structural_evidence["boltz2_iptm_model_0"] is None
    assert result.baseline_method == "ipsae"
    assert result.ranking_method == "ipsae"
    assert result.is_probability is False
    assert result.recommended_as_hard_filter is False
    assert result.structural_evidence["ipsae"] == 0.887
    assert result.structural_evidence["iptm_single"] == 0.931
    assert result.structural_evidence["boltz2_iptm_mean"] == 0.98
    assert result.additional_evidence["boltz2_iptm_mean"] == 0.98
    assert set(result.structural_evidence) == set(STRUCTURAL_EVIDENCE_KEYS)
    assert set(result.additional_evidence) == set(ADDITIONAL_EVIDENCE_KEYS)
    assert "additional evidence" in " ".join(result.limitations)


def test_v2_rank_uses_ipsae_even_when_ensemble_disagrees():
    tool = ProteinScoringToolV2()

    low_ipsae = _candidate("low", {"ipsae": 0.20, "iptm": 0.99})
    high_ipsae = _candidate("high", {"ipsae": 0.90, "iptm": 0.10})

    ensembles = {
        "low": summarize_boltz2_ensemble(
            [Boltz2Sample(sample_id="a", iptm=0.99)]
        ),
        "high": summarize_boltz2_ensemble(
            [Boltz2Sample(sample_id="b", iptm=0.10)]
        ),
    }

    ranked = tool.rank([low_ipsae, high_ipsae], ensembles)

    assert [result.candidate_id for result in ranked] == ["high", "low"]
    assert ranked[0].baseline_score == 0.90


def test_v2_rank_breaks_ipsae_ties_by_candidate_id():
    tool = ProteinScoringToolV2()

    ranked = tool.rank(
        [
            _candidate("B", {"ipsae": 0.90}),
            _candidate("A", {"ipsae": 0.90}),
        ]
    )

    assert [result.candidate_id for result in ranked] == ["A", "B"]


def _ensemble_with_models(
    *samples: tuple[int, float],
) -> Boltz2EnsembleSummary:
    return summarize_boltz2_ensemble(
        [
            Boltz2Sample(
                sample_id=f"confidence_binder_model_{index}",
                iptm=iptm,
                model_index=index,
            )
            for index, iptm in samples
        ]
    )


def test_v2_exposes_model_0_iptm():
    tool = ProteinScoringToolV2()
    ensemble = _ensemble_with_models(
        (0, 0.41),
        (1, 0.90),
        (2, 0.88),
    )

    result = tool.score(
        _candidate("binder-001", {"ipsae": 0.887, "iptm": 0.931}),
        ensemble,
    )

    assert result.ranking_score == 0.887
    assert result.ranking_score == result.structural_evidence["ipsae"]
    assert result.structural_evidence["boltz2_iptm_model_0"] == 0.41
    assert result.additional_evidence["boltz2_iptm_model_0"] == 0.41
    assert result.is_probability is False
    assert result.recommended_as_hard_filter is False


def test_v2_model_0_is_index_not_list_position():
    tool = ProteinScoringToolV2()
    ensemble = _ensemble_with_models(
        (3, 0.99),
        (0, 0.41),
        (4, 0.10),
        (1, 0.50),
    )

    result = tool.score(
        _candidate("binder-001", {"ipsae": 0.75}),
        ensemble,
    )

    assert result.structural_evidence["boltz2_iptm_model_0"] == 0.41
    assert result.structural_evidence["boltz2_iptm_model_0"] != (
        ensemble.iptm_mean
    )
    assert result.ranking_score == 0.75


def test_v2_rank_ignores_model_0_iptm():
    tool = ProteinScoringToolV2()

    candidate_a = _candidate("A", {"ipsae": 0.90})
    candidate_b = _candidate("B", {"ipsae": 0.80})

    ensembles = {
        "A": _ensemble_with_models((0, 0.20), (1, 0.21)),
        "B": _ensemble_with_models((0, 0.99), (1, 0.98)),
    }

    ranked = tool.rank(
        [candidate_b, candidate_a],
        ensembles,
    )

    assert [result.candidate_id for result in ranked] == ["A", "B"]
    assert ranked[0].ranking_score == 0.90
    assert ranked[0].structural_evidence["boltz2_iptm_model_0"] == 0.20
    assert ranked[1].structural_evidence["boltz2_iptm_model_0"] == 0.99
    assert ranked[0].is_probability is False
    assert ranked[0].recommended_as_hard_filter is False


def test_v2_missing_model_0_is_none_not_substituted():
    tool = ProteinScoringToolV2()
    ensemble = _ensemble_with_models(
        (1, 0.99),
        (2, 0.98),
    )

    result = tool.score(
        _candidate("binder-001", {"ipsae": 0.80}),
        ensemble,
    )

    assert result.ranking_score == 0.80
    assert result.structural_evidence["boltz2_iptm_model_0"] is None
    assert result.additional_evidence["boltz2_iptm_model_0"] is None
    assert result.structural_evidence["boltz2_iptm_mean"] == pytest.approx(
        0.985
    )
    assert any(
        "Missing Boltz2 model_0 sample" in warning
        for warning in result.warnings
    )


def test_v2_missing_ensemble_leaves_additional_evidence_empty():
    tool = ProteinScoringToolV2()

    result = tool.score(
        _candidate("missing-ensemble", {"ipsae": 0.80, "iptm": 0.70})
    )

    assert result.baseline_score == 0.80
    assert result.structural_evidence["boltz2_iptm_mean"] is None
    assert result.structural_evidence["boltz2_iptm_model_0"] is None
    assert result.additional_evidence["boltz2_iptm_mean"] is None
    assert result.additional_evidence["boltz2_iptm_model_0"] is None
    assert any("Missing Boltz2 ensemble" in warning for warning in result.warnings)


def test_boltz2_yaml_is_target_plus_binder_complex():
    yaml_text = build_boltz2_complex_yaml(
        target_sequence="CSQNEYFDSLLHACIPCQLRCSSNTPPLTCQRYCNA",
        binder_sequence="MKTIIALSYIFCLVFAD",
    )

    assert "version: 1" in yaml_text
    assert "id: A" in yaml_text
    assert "id: B" in yaml_text
    assert "CSQNEYFDSLLHACIPCQLRCSSNTPPLTCQRYCNA" in yaml_text
    assert "MKTIIALSYIFCLVFAD" in yaml_text
    assert yaml_text.count("msa: empty") == 1
    assert "msa: empty" not in yaml_text.split("id: B", 1)[0]
    assert "msa: empty" in yaml_text.split("id: B", 1)[1]
    assert "ysd" not in yaml_text.lower()


def test_boltz2_yaml_can_point_to_frozen_target_msa():
    yaml_text = build_boltz2_complex_yaml(
        target_sequence="CSQNEYFDSLLHACIPCQLRCSSNTPPLTCQRYCNA",
        binder_sequence="MKTIIALSYIFCLVFAD",
        target_msa="data/processed/boltz2/bcma_target.csv",
    )

    target_block, binder_block = yaml_text.split("id: B", 1)

    assert (
        "msa: data/processed/boltz2/bcma_target.csv"
        in target_block
    )
    assert "msa: empty" in binder_block
    assert target_block.count("msa:") == 1


def test_write_boltz2_yaml(tmp_path):
    path = write_boltz2_yaml(
        _candidate("BCMA_l59_int_0480"),
        tmp_path,
    )

    assert path.name == "BCMA_l59_int_0480.yaml"
    assert path.read_text().startswith("version: 1")
