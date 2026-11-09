import json
import shutil
from pathlib import Path

import pytest

from cellertz.domains.protein_design.adapters.bindcraft import (
    BindCraftAdapter,
    BindCraftArtifacts,
    ExistingBindCraftArtifactsExecutor,
)
from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
    ProteinGenerationAgent,
)
from cellertz.domains.protein_design.agents.supervisor import (
    ProteinSupervisorV1,
)
from cellertz.domains.protein_design.evidence.bindcraft_ipsae import (
    BindCraftArtifactResolver,
    BindCraftIpSAEEvidenceProvider,
)
from cellertz.domains.protein_design.scoring.bindcraft_ipsae import (
    BindCraftIpSAEScoreProvider,
)
from cellertz.tools.protein_scoring.ipsae import IpSAEConfig

FIXTURE_ROOT = Path(__file__).parent / "fixtures"

GPU3_STATS = (
    FIXTURE_ROOT
    / "bindcraft_gpu3"
    / "final_design_stats.csv"
)

GPU4_EVIDENCE = (
    FIXTURE_ROOT
    / "ipsae_gpu4"
)

DESIGN = "PDL1_l65_s985772_mpnn1"
CANDIDATE_ID = f"bindcraft_{DESIGN}"
EXECUTION_ID = "exec_pdl1_smoke_gpu4"
RUN_ID = "protein_run_pdl1_gpu4_integration"


def _materialize_real_execution_layout(
    output_root: Path,
) -> BindCraftArtifacts:
    designs_dir = (
        output_root
        / EXECUTION_ID
        / "designs"
    )

    pdb_dir = designs_dir / "MPNN" / "Relaxed"
    pae_dir = designs_dir / "CellertzEvidence" / "PAE"

    pdb_dir.mkdir(parents=True)
    pae_dir.mkdir(parents=True)

    stats_csv = designs_dir / "final_design_stats.csv"
    shutil.copyfile(
        GPU3_STATS,
        stats_csv,
    )

    for model in (1, 2):
        shutil.copyfile(
            GPU4_EVIDENCE / f"{DESIGN}_model{model}.pdb",
            pdb_dir / f"{DESIGN}_model{model}.pdb",
        )
        shutil.copyfile(
            GPU4_EVIDENCE / f"{DESIGN}_model{model}_pae.npz",
            pae_dir / f"{DESIGN}_model{model}_pae.npz",
        )

    return BindCraftArtifacts(
        output_dir=designs_dir,
        stats_csv=stats_csv,
        command=("frozen-gpu4-bindcraft",),
        bindcraft_version="frozen_gpu4",
        returncode=0,
        execution_provenance={
            "execution_id": EXECUTION_ID,
            "run_id": RUN_ID,
        },
    )


def _request() -> GenerationRequest:
    return GenerationRequest(
        target_id="PDL1",
        objective="binder_design",
        generator="bindcraft",
        n_candidates=1,
        constraints={
            "length_min": 65,
            "length_max": 65,
        },
    )


def _score_fn(
    output_root: Path,
) -> BindCraftIpSAEScoreProvider:
    evidence_provider = BindCraftIpSAEEvidenceProvider(
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

    return BindCraftIpSAEScoreProvider(
        evidence_provider=evidence_provider,
    )


def test_frozen_gpu4_bindcraft_traverses_supervisor_with_real_ipsae(
    tmp_path,
):
    output_root = tmp_path / "scientific_execution"

    artifacts = _materialize_real_execution_layout(
        output_root
    )

    generation_agent = ProteinGenerationAgent(
        adapters={
            "bindcraft": BindCraftAdapter(
                executor=ExistingBindCraftArtifactsExecutor(
                    artifacts
                )
            )
        }
    )

    run_dir = tmp_path / "supervisor_run"

    supervisor = ProteinSupervisorV1(
        request=_request(),
        artifact_dir=run_dir,
        run_id=RUN_ID,
        generation_agent=generation_agent,
        score_fn=_score_fn(output_root),
    )

    run = supervisor.run()

    assert run.generation.status == "complete"
    assert run.scoring.status == "complete"
    assert run.ranking.status == "complete"
    assert run.critique.status == "complete"
    assert run.prioritization.status == "complete"

    generation = supervisor.generation_result
    assert generation is not None
    assert len(generation.candidates) == 1

    candidate = generation.candidates[0]

    assert candidate.candidate_id == CANDIDATE_ID
    assert (
        candidate.generator_metadata["provenance"]["execution"][
            "execution_id"
        ]
        == EXECUTION_ID
    )

    assert len(supervisor.score_results) == 1

    score = supervisor.score_results[0]

    assert score.candidate_id == CANDIDATE_ID
    assert score.ranking_score == pytest.approx(
        0.013557,
        abs=5e-7,
    )
    assert score.structural_evidence["ipsae"] == pytest.approx(
        0.013557,
        abs=5e-7,
    )

    evidence = score.provenance["ipsae_evidence"]

    assert evidence["execution_id"] == EXECUTION_ID
    assert evidence["protocol_id"] == "CARPNN_2026_REFERENCE"
    assert evidence["model_aggregation"] == "mean"
    assert evidence["n_models"] == 2

    assert evidence["ipsae_mean"] == pytest.approx(
        0.013557,
        abs=5e-7,
    )
    assert evidence["ipsae_min"] == pytest.approx(
        0.010173,
        abs=5e-7,
    )
    assert evidence["ipsae_max"] == pytest.approx(
        0.016941,
        abs=5e-7,
    )

    assert [
        model["model_number"]
        for model in evidence["models"]
    ] == [1, 2]

    scoring_payload = json.loads(
        (run_dir / "scoring.json").read_text()
    )

    assert len(scoring_payload) == 1

    persisted = scoring_payload[0]

    assert persisted["candidate_id"] == CANDIDATE_ID
    assert persisted["ranking_score"] == pytest.approx(
        0.013557,
        abs=5e-7,
    )

    persisted_evidence = persisted["provenance"][
        "ipsae_evidence"
    ]

    assert persisted_evidence["execution_id"] == EXECUTION_ID
    assert persisted_evidence["n_models"] == 2

    assert persisted_evidence["models"][0]["pae_sha256"] == (
        "dbf52bfd7910b49e5c87ec8569965be9"
        "a56ad53d4c65497584afbebd34b4e2a2"
    )
    assert persisted_evidence["models"][1]["pae_sha256"] == (
        "7b14c58cee82945883adc1e5e5e17e13"
        "dd11a7672b52d9decfa5467a510ce9a2"
    )

    assert len(supervisor.ranking_records) == 1

    ranked = supervisor.ranking_records[0]

    assert ranked.candidate_id == CANDIDATE_ID
    assert ranked.rank == 1
    assert ranked.ranking_score == pytest.approx(
        0.013557,
        abs=5e-7,
    )
    assert ranked.ranking_score == ranked.structural_evidence["ipsae"]
