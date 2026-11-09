import csv
import sys
from pathlib import Path

import pytest

from cellertz.domains.protein_design.adapters.mber import (
    ADAPTER_VERSION,
    FixtureMBERExecutor,
    LocalMBERExecutor,
    MBERAdapter,
    MBERArtifacts,
    MBERError,
    MBERParser,
    STATS_FILENAME,
)
from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
    ProteinGenerationAgent,
    request_input_hash,
)
from cellertz.domains.protein_design.agents.supervisor import ProteinSupervisorV1
from cellertz.tools.protein_scoring.scoring import RANKING_METHOD


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "mber"

EXPECTED_IDS = [
    "mber_trajectory_20240315_143022_a3f9b2_binder_0",
    "mber_trajectory_20240315_143055_b7c4d1_binder_1",
    "mber_trajectory_20240315_143118_c1d2e3_binder_2",
]


def _request(
    n_candidates: int = 3,
    constraints: dict | None = None,
) -> GenerationRequest:
    return GenerationRequest(
        target_id="BCMA",
        objective="binder_design",
        generator="mber",
        n_candidates=n_candidates,
        constraints=constraints or {
            "length_min": 100,
            "length_max": 140,
        },
    )


def _adapter(tmp_path: Path | None = None) -> MBERAdapter:
    return MBERAdapter(
        executor=FixtureMBERExecutor(FIXTURE_DIR),
        work_dir=tmp_path,
    )


def _write_accepted(path: Path, rows: list[dict[str, str]]) -> Path:
    fieldnames = list(rows[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_parser_is_deterministic():
    parser = MBERParser()
    artifacts = MBERArtifacts(
        output_dir=FIXTURE_DIR,
        accepted_csv=FIXTURE_DIR / STATS_FILENAME,
        command=("fixture-mber",),
        mber_version="fixture_outputs_v1",
    )
    request = _request()

    first = parser.parse(request, artifacts)
    second = parser.parse(request, artifacts)

    assert [item.to_dict() for item in first] == [
        item.to_dict() for item in second
    ]
    assert [item.candidate_id for item in first] == EXPECTED_IDS


def test_parser_does_not_rank_by_mber_iptm():
    candidates = _adapter().generate(_request())
    iptm = [
        float(item.generator_metadata["native_metrics"]["i_ptm"])
        for item in candidates
    ]

    assert iptm != sorted(iptm, reverse=True)
    assert candidates[0].candidate_id.endswith("a3f9b2_binder_0")


def test_mber_fields_stay_in_generator_metadata():
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
    assert candidate.generator == "mber"
    assert metadata["source_file"] == STATS_FILENAME
    assert metadata["trajectory_name"] == "trajectory_20240315_143022_a3f9b2"
    assert metadata["binder_index"] == "0"
    assert metadata["native_metrics"]["i_ptm"] == "0.8123"
    assert provenance["adapter_version"] == ADAPTER_VERSION
    assert provenance["command"] == ["fixture-mber"]
    assert provenance["input_hash"] == request_input_hash(_request(1))
    assert "ranking_score" not in payload
    assert "rank" not in payload
    assert "ipsae" not in metadata
    assert "ipsae" not in metadata["native_metrics"]
    assert not hasattr(candidate, "ranking_score")


def test_duplicate_designs_are_rejected(tmp_path):
    stats = _write_accepted(
        tmp_path / STATS_FILENAME,
        [
            {
                "trajectory_name": "same_traj",
                "binder_index": "0",
                "binder_seq": "EVQLVESGGGLVQPGGSLRLSCAASGFTFSSYAMAWFRQAPGKGLEWVSA",
            },
            {
                "trajectory_name": "same_traj",
                "binder_index": "0",
                "binder_seq": "QVQLVESGGGLVQPGGSLRLSCAASGFTFSSYAMAWFRQAPGKGLEWVSA",
            },
        ],
    )
    parser = MBERParser()
    artifacts = MBERArtifacts(
        output_dir=tmp_path,
        accepted_csv=stats,
        command=("fixture-mber",),
    )

    with pytest.raises(MBERError, match="Duplicate"):
        parser.parse(_request(2), artifacts)


def test_invalid_sequence_is_rejected(tmp_path):
    stats = _write_accepted(
        tmp_path / STATS_FILENAME,
        [
            {
                "trajectory_name": "bad",
                "binder_index": "0",
                "binder_seq": "EVQL123",
            }
        ],
    )
    parser = MBERParser()
    artifacts = MBERArtifacts(
        output_dir=tmp_path,
        accepted_csv=stats,
        command=("fixture-mber",),
    )

    with pytest.raises(MBERError, match="invalid residues"):
        parser.parse(_request(1), artifacts)


def test_process_error_becomes_failed_generation_result(tmp_path):
    script = tmp_path / "failing_mber.py"
    script.write_text("import sys\nsys.exit(2)\n")
    request = _request(
        1,
        constraints={"mber_command": [sys.executable, str(script)]},
    )
    result = ProteinGenerationAgent(
        adapters={
            "mber": MBERAdapter(
                executor=LocalMBERExecutor(),
                work_dir=tmp_path / "work",
            )
        }
    ).generate(request)

    assert result.status == "failed"
    assert result.candidates == ()
    assert result.adapter_name == "mber"
    assert result.error is not None
    assert "return code 2" in result.error


def test_unconfigured_local_executor_fails_without_gpu():
    result = ProteinGenerationAgent().generate(_request())

    assert result.status == "failed"
    assert result.candidates == ()
    assert result.adapter_name == "mber"
    assert result.error is not None
    assert "not configured" in result.error.lower()


def test_local_executor_parses_command_output(tmp_path):
    script = tmp_path / "fake_mber.py"
    script.write_text(
        "import shutil\n"
        "import sys\n"
        "from pathlib import Path\n"
        "shutil.copy(sys.argv[1], Path('accepted.csv'))\n"
    )
    work_dir = tmp_path / "work"
    request = _request(
        3,
        constraints={
            "mber_command": [
                sys.executable,
                str(script),
                str(FIXTURE_DIR / STATS_FILENAME),
            ],
            "mber_version": "fake_local_v1",
        },
    )
    result = ProteinGenerationAgent(
        adapters={
            "mber": MBERAdapter(
                executor=LocalMBERExecutor(),
                work_dir=work_dir,
            )
        }
    ).generate(request)

    assert result.status == "complete"
    assert result.adapter_name == "mber"
    assert len(result.candidates) == 3
    assert (
        result.candidates[0].generator_metadata["provenance"][
            "mber_version"
        ]
        == "fake_local_v1"
    )
    assert (work_dir / STATS_FILENAME).is_file()


def test_mber_fixture_traverses_supervisor(tmp_path):
    supervisor = ProteinSupervisorV1(
        request=_request(),
        artifact_dir=tmp_path / "run",
        run_id="protein_run_mber_001",
        generation_agent=ProteinGenerationAgent(
            adapters={"mber": _adapter(tmp_path / "mber")}
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
    assert generation.adapter_name == "mber"
    for record in supervisor.ranking_records:
        assert record.ranking_signal == RANKING_METHOD
        assert record.ranking_score == record.structural_evidence["ipsae"]
        assert record.ranking_score != float(
            generation.candidates[0].generator_metadata["native_metrics"]["i_ptm"]
        )
    for record in supervisor.critic_records:
        assert (
            record.rank,
            record.ranking_score,
        ) == ranked[record.candidate_id]
    supervisor_source = Path(
        "src/cellertz/domains/protein_design/agents/supervisor.py"
    ).read_text()
    assert "MBERAdapter" not in supervisor_source
    assert "from cellertz.domains.protein_design.adapters.mber" not in supervisor_source
