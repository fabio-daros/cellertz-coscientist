import shutil
from pathlib import Path

from cellertz.domains.protein_design.agents.generation import (
    NormalizedCandidate,
)
from cellertz.domains.protein_design.evidence.bindcraft_ipsae import (
    BindCraftArtifactResolver,
    BindCraftIpSAEEvidenceProvider,
)
from cellertz.domains.protein_design.scoring.bindcraft_ipsae import (
    BindCraftIpSAEScoreProvider,
)
from cellertz.tools.protein_scoring.ipsae import IpSAEConfig

FIXTURE_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "ipsae_gpu4"
)

DESIGN = "PDL1_l65_s985772_mpnn1"
EXECUTION_ID = "exec_pdl1_smoke_gpu4"


def _candidate() -> NormalizedCandidate:
    return NormalizedCandidate(
        candidate_id=f"bindcraft_{DESIGN}",
        sequence=(
            "SSLFEKIVKWAMEFTVAMKEYMEKNPELTDEQIRTIQKEVM"
            "RRFEAKDFGDPRKLIEEALAKGPP"
        ),
        target_id="PDL1",
        generator="bindcraft",
        generator_metadata={
            "design": DESIGN,
            "provenance": {
                "execution": {
                    "execution_id": EXECUTION_ID,
                },
            },
        },
    )


def _materialize_execution_layout(root: Path) -> None:
    designs_dir = (
        root
        / EXECUTION_ID
        / "designs"
    )

    pdb_dir = designs_dir / "MPNN" / "Relaxed"
    pae_dir = designs_dir / "CellertzEvidence" / "PAE"

    pdb_dir.mkdir(parents=True)
    pae_dir.mkdir(parents=True)

    for model in (1, 2):
        shutil.copyfile(
            FIXTURE_DIR / f"{DESIGN}_model{model}.pdb",
            pdb_dir / f"{DESIGN}_model{model}.pdb",
        )
        shutil.copyfile(
            FIXTURE_DIR / f"{DESIGN}_model{model}_pae.npz",
            pae_dir / f"{DESIGN}_model{model}_pae.npz",
        )


def _evidence_provider(
    output_root: Path,
) -> BindCraftIpSAEEvidenceProvider:
    return BindCraftIpSAEEvidenceProvider(
        resolver=BindCraftArtifactResolver(
            output_root=output_root,
        ),
        ipsae_config=IpSAEConfig(
            pae_cutoff=10.0,
            distance_cutoff=10.0,
            chain_a="A",
            chain_b="B",
            aggregation="max",
            protocol_id="CARPNN_2026_REFERENCE",
        ),
    )


def test_bindcraft_ipsae_score_provider_uses_average_ipsae(
    tmp_path,
):
    _materialize_execution_layout(tmp_path)

    score_fn = BindCraftIpSAEScoreProvider(
        evidence_provider=_evidence_provider(tmp_path),
    )

    results = score_fn((_candidate(),))

    assert len(results) == 1

    result = results[0]

    assert result.candidate_id == f"bindcraft_{DESIGN}"
    assert round(result.ranking_score, 6) == 0.013557
    assert round(
        result.structural_evidence["ipsae"],
        6,
    ) == 0.013557

    assert result.is_probability is False
    assert result.recommended_as_hard_filter is False


def test_bindcraft_ipsae_score_provider_preserves_provenance(
    tmp_path,
):
    _materialize_execution_layout(tmp_path)

    score_fn = BindCraftIpSAEScoreProvider(
        evidence_provider=_evidence_provider(tmp_path),
    )

    result = score_fn((_candidate(),))[0]

    evidence = result.provenance["ipsae_evidence"]

    assert evidence["execution_id"] == EXECUTION_ID
    assert evidence["design"] == DESIGN
    assert evidence["protocol_id"] == "CARPNN_2026_REFERENCE"
    assert evidence["model_aggregation"] == "mean"
    assert evidence["n_models"] == 2

    assert round(evidence["ipsae_mean"], 6) == 0.013557
    assert round(evidence["ipsae_min"], 6) == 0.010173
    assert round(evidence["ipsae_max"], 6) == 0.016941

    assert [
        model["model_number"]
        for model in evidence["models"]
    ] == [1, 2]

    assert round(
        evidence["models"][0]["ipsae"],
        6,
    ) == 0.010173
    assert round(
        evidence["models"][1]["ipsae"],
        6,
    ) == 0.016941

    assert evidence["models"][0]["pae_sha256"] == (
        "dbf52bfd7910b49e5c87ec8569965be9"
        "a56ad53d4c65497584afbebd34b4e2a2"
    )
    assert evidence["models"][0]["pdb_sha256"] == (
        "3b2363eb9bf50840926db0af8acb2abf"
        "d170680d1f51bb8c4b5e75c9c5491a4d"
    )
