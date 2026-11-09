import shutil
import sys
from pathlib import Path

import pytest

from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
    ProteinGenerationAgent,
    request_input_hash,
)
from cellertz.domains.protein_design.agents.supervisor import ProteinSupervisorV1
from cellertz.domains.protein_design.adapters.rfdiffusion import (
    ADAPTER_VERSION,
    FixtureRFdiffusionExecutor,
    LocalRFdiffusionExecutor,
    RFdiffusionAdapter,
    RFdiffusionArtifacts,
    RFdiffusionError,
    RFdiffusionParser,
)
from cellertz.tools.protein_scoring.scoring import RANKING_METHOD


FIXTURE_ROOT = (
    Path(__file__).resolve().parent / "fixtures" / "rfdiffusion"
)
SEQUENCED_DIR = FIXTURE_ROOT / "sequenced"
BACKBONE_DIR = FIXTURE_ROOT / "backbone_only"

EXPECTED_IDS = [
    "rfdiffusion_BCMAE2_1xu2_R14-R18-R20_30-80_i101_33",
    "rfdiffusion_BCMAE2_1xu2_R14-R18-R20_30-80_i102_172",
    "rfdiffusion_BCMAE2_1xu2_R14-R18-R20_30-80_i10_109",
]


def _request(
    n_candidates: int = 3,
    constraints: dict | None = None,
) -> GenerationRequest:
    return GenerationRequest(
        target_id="BCMA",
        objective="binder_design",
        generator="rfdiffusion",
        n_candidates=n_candidates,
        constraints=constraints or {
            "length_min": 30,
            "length_max": 100,
            "binder_chain": "B",
        },
    )


def _adapter(
    artifact_dir: Path = SEQUENCED_DIR,
    tmp_path: Path | None = None,
) -> RFdiffusionAdapter:
    return RFdiffusionAdapter(
        executor=FixtureRFdiffusionExecutor(artifact_dir),
        work_dir=tmp_path,
    )


def test_parser_is_deterministic():
    parser = RFdiffusionParser()
    artifacts = RFdiffusionArtifacts(
        output_dir=SEQUENCED_DIR,
        command=("fixture-rfdiffusion",),
        rfdiffusion_version="fixture_outputs_v1",
    )
    request = _request()

    first = parser.parse(request, artifacts)
    second = parser.parse(request, artifacts)

    assert [item.to_dict() for item in first] == [
        item.to_dict() for item in second
    ]
    assert [item.candidate_id for item in first] == EXPECTED_IDS


def test_sequence_comes_from_fasta_not_backbone_placeholder():
    candidates = _adapter().generate(_request())
    first = candidates[0]

    assert first.sequence.startswith("SRRTAVVLVG")
    assert set(first.sequence) != {"G"}
    assert first.generator_metadata["backbone_sequence"]
    assert set(first.generator_metadata["backbone_sequence"]) == {"G"}
    assert first.generator_metadata["sequence_source"] == "proteinmpnn"
    assert first.generator_metadata["sequence_source_version"] == "proteinmpnn_v1"
    assert first.generator_metadata["structure_generator"] == "rfdiffusion"
    assert first.sequence != first.generator_metadata["backbone_sequence"]


def test_backbone_only_artifacts_do_not_invent_a_sequence():
    parser = RFdiffusionParser()
    artifacts = RFdiffusionArtifacts(
        output_dir=BACKBONE_DIR,
        command=("fixture-rfdiffusion",),
    )

    with pytest.raises(
        RFdiffusionError,
        match="no designed sequence",
    ):
        parser.parse(_request(1), artifacts)


def test_backbone_only_fails_generation_without_schema_change():
    result = ProteinGenerationAgent(
        adapters={"rfdiffusion": _adapter(BACKBONE_DIR)}
    ).generate(_request(1))

    assert result.status == "failed"
    assert result.candidates == ()
    assert result.error is not None
    assert "no designed sequence" in result.error


def test_rfdiffusion_fields_stay_in_generator_metadata():
    candidate = _adapter().generate(_request(1))[0]
    payload = candidate.to_dict()
    metadata = candidate.generator_metadata
    provenance = metadata["provenance"]

    assert set(payload) == {
        "candidate_id",
        "sequence",
        "target_id",
        "generator",
        "generator_metadata",
    }
    assert candidate.generator == "rfdiffusion"
    assert metadata["structure_path"] == "i101_33.pdb"
    assert metadata["contig"] == "A14,A18,A20/0 30-80"
    assert metadata["target"] == "BCMA"
    assert metadata["seed"] == 33
    assert metadata["diffusion_parameters"]["final_step"] == 50
    assert metadata["native_metrics"]["plddt"] == 0.72
    assert metadata["structure_generator"] == "rfdiffusion"
    assert metadata["sequence_source"] == "proteinmpnn"
    assert metadata["sequence_source_version"] == "proteinmpnn_v1"
    assert provenance["adapter_version"] == ADAPTER_VERSION
    assert provenance["command"] == ["fixture-rfdiffusion"]
    assert provenance["input_hash"] == request_input_hash(_request(1))
    assert "ranking_score" not in payload
    assert "rank" not in payload
    assert "ipsae" not in metadata
    assert "ipsae" not in metadata["native_metrics"]
    assert "design_stage" not in payload
    assert not hasattr(candidate, "ranking_score")
    assert not hasattr(candidate, "design_stage")


def test_duplicate_designs_are_rejected(tmp_path):
    shutil.copy(SEQUENCED_DIR / "i101_33.pdb", tmp_path / "a.pdb")
    shutil.copy(SEQUENCED_DIR / "i101_33.fa", tmp_path / "a.fa")
    shutil.copy(SEQUENCED_DIR / "i101_33.json", tmp_path / "a.json")
    shutil.copy(SEQUENCED_DIR / "i101_33.pdb", tmp_path / "b.pdb")
    shutil.copy(SEQUENCED_DIR / "i101_33.fa", tmp_path / "b.fa")
    shutil.copy(SEQUENCED_DIR / "i101_33.json", tmp_path / "b.json")
    parser = RFdiffusionParser()
    artifacts = RFdiffusionArtifacts(
        output_dir=tmp_path,
        command=("fixture-rfdiffusion",),
    )

    with pytest.raises(RFdiffusionError, match="Duplicate"):
        parser.parse(_request(2), artifacts)


def test_process_error_becomes_failed_generation_result(tmp_path):
    script = tmp_path / "failing_rfdiffusion.py"
    script.write_text("import sys\nsys.exit(2)\n")
    request = _request(
        1,
        constraints={
            "rfdiffusion_command": [sys.executable, str(script)],
        },
    )
    agent = ProteinGenerationAgent(
        adapters={
            "rfdiffusion": RFdiffusionAdapter(
                executor=LocalRFdiffusionExecutor(),
                work_dir=tmp_path / "work",
            )
        }
    )
    result = agent.generate(request)

    assert result.status == "failed"
    assert result.candidates == ()
    assert result.adapter_name == "rfdiffusion"
    assert result.error is not None
    assert "return code 2" in result.error


def test_unconfigured_local_executor_fails_without_gpu():
    result = ProteinGenerationAgent().generate(_request())

    assert result.status == "failed"
    assert result.candidates == ()
    assert result.adapter_name == "rfdiffusion"
    assert result.error is not None
    assert "not configured" in result.error.lower()


def test_local_executor_parses_command_output(tmp_path):
    script = tmp_path / "fake_rfdiffusion.py"
    script.write_text(
        "import shutil\n"
        "import sys\n"
        "from pathlib import Path\n"
        "src = Path(sys.argv[1])\n"
        "for path in src.iterdir():\n"
        "    shutil.copy(path, Path(path.name))\n"
    )
    work_dir = tmp_path / "work"
    request = _request(
        3,
        constraints={
            "rfdiffusion_command": [
                sys.executable,
                str(script),
                str(SEQUENCED_DIR),
            ],
            "rfdiffusion_version": "fake_local_v1",
        },
    )
    agent = ProteinGenerationAgent(
        adapters={
            "rfdiffusion": RFdiffusionAdapter(
                executor=LocalRFdiffusionExecutor(),
                work_dir=work_dir,
            )
        }
    )
    result = agent.generate(request)

    assert result.status == "complete"
    assert result.adapter_name == "rfdiffusion"
    assert len(result.candidates) == 3
    assert (
        result.candidates[0].generator_metadata["provenance"][
            "rfdiffusion_version"
        ]
        == "fake_local_v1"
    )
    assert (work_dir / "i101_33.pdb").is_file()


def test_generation_agent_normalizes_fixture_rfdiffusion():
    result = ProteinGenerationAgent(
        adapters={"rfdiffusion": _adapter()}
    ).generate(_request())

    assert result.status == "complete"
    assert result.adapter_version == ADAPTER_VERSION
    assert [
        item.candidate_id for item in result.candidates
    ] == EXPECTED_IDS
    assert result.input_hash == request_input_hash(_request())
    for candidate in result.candidates:
        assert candidate.generator == "rfdiffusion"
        assert candidate.target_id == "BCMA"
        assert candidate.sequence


def test_rfdiffusion_fixture_traverses_supervisor(tmp_path):
    supervisor = ProteinSupervisorV1(
        request=_request(),
        artifact_dir=tmp_path / "run",
        run_id="protein_run_rfdiffusion_001",
        generation_agent=ProteinGenerationAgent(
            adapters={"rfdiffusion": _adapter(tmp_path=tmp_path / "rfd")}
        ),
    )
    run = supervisor.run()
    generation = supervisor.generation_result
    ranked = {
        record.candidate_id: (record.rank, record.ranking_score)
        for record in supervisor.ranking_records
    }

    assert run.generation.status == "complete"
    assert run.scoring.status == "complete"
    assert run.ranking.status == "complete"
    assert run.critique.status == "complete"
    assert run.prioritization.status == "complete"
    assert generation is not None
    assert generation.adapter_name == "rfdiffusion"
    for record in supervisor.ranking_records:
        assert record.ranking_signal == RANKING_METHOD
        assert record.ranking_score == record.structural_evidence["ipsae"]
    for record in supervisor.critic_records:
        assert (
            record.rank,
            record.ranking_score,
        ) == ranked[record.candidate_id]
    supervisor_source = Path(
        "src/cellertz/domains/protein_design/agents/supervisor.py"
    ).read_text()
    assert "RFdiffusionAdapter" not in supervisor_source
    assert "from cellertz.domains.protein_design.adapters.rfdiffusion" not in supervisor_source
