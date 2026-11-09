import csv
import sys
from pathlib import Path

import pytest

from cellertz.domains.protein_design.adapters.bindcraft import (
    ADAPTER_VERSION,
    STATS_FILENAME,
    BindCraftAdapter,
    BindCraftArtifacts,
    BindCraftError,
    BindCraftParser,
    FixtureBindCraftExecutor,
    LocalBindCraftExecutor,
)
from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
    ProteinGenerationAgent,
    request_input_hash,
)
from cellertz.domains.protein_design.agents.supervisor import ProteinSupervisorV1
from cellertz.tools.protein_scoring.scoring import RANKING_METHOD

FIXTURE_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "bindcraft"
)


def _request(
    n_candidates: int = 3,
    constraints: dict | None = None,
) -> GenerationRequest:
    return GenerationRequest(
        target_id="BCMA",
        objective="binder_design",
        generator="bindcraft",
        n_candidates=n_candidates,
        constraints=constraints or {
            "length_min": 50,
            "length_max": 100,
        },
    )


def _adapter(tmp_path: Path | None = None) -> BindCraftAdapter:
    return BindCraftAdapter(
        executor=FixtureBindCraftExecutor(FIXTURE_DIR),
        work_dir=tmp_path,
    )


def _write_stats(path: Path, rows: list[dict[str, str]]) -> Path:
    fieldnames = list(rows[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_parser_is_deterministic():
    parser = BindCraftParser()
    artifacts = BindCraftArtifacts(
        output_dir=FIXTURE_DIR,
        stats_csv=FIXTURE_DIR / STATS_FILENAME,
        command=("fixture-bindcraft",),
        bindcraft_version="fixture_outputs_v1",
    )
    request = _request()

    first = parser.parse(request, artifacts)
    second = parser.parse(request, artifacts)

    assert [item.to_dict() for item in first] == [
        item.to_dict() for item in second
    ]
    assert [
        item.candidate_id for item in first
    ] == [
        "bindcraft_BCMA_l50_s161492_mpnn14",
        "bindcraft_BCMA_l50_s161492_mpnn1",
        "bindcraft_BCMA_l51_s395146_mpnn5",
    ]


def test_parser_does_not_rank_by_bindcraft_metrics():
    parser = BindCraftParser()
    artifacts = BindCraftArtifacts(
        output_dir=FIXTURE_DIR,
        stats_csv=FIXTURE_DIR / STATS_FILENAME,
        command=("fixture-bindcraft",),
    )
    candidates = parser.parse(_request(), artifacts)
    iptm = [
        float(item.generator_metadata["native_metrics"]["Average_i_pTM"])
        for item in candidates
    ]

    assert iptm != sorted(iptm, reverse=True)
    assert candidates[0].candidate_id.endswith("mpnn14")


def test_candidate_ids_are_stable_and_unique():
    candidates = _adapter().generate(_request())
    ids = [item.candidate_id for item in candidates]

    assert len(ids) == len(set(ids))
    assert all(item.startswith("bindcraft_") for item in ids)
    assert all(item.sequence for item in candidates)
    assert all(
        set(item.sequence) <= set("ACDEFGHIKLMNPQRSTVWY")
        for item in candidates
    )


def test_bindcraft_fields_stay_in_generator_metadata():
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
    assert candidate.generator == "bindcraft"
    assert metadata["source_file"] == STATS_FILENAME
    assert metadata["design"] == "BCMA_l50_s161492_mpnn14"
    assert metadata["design_number"] == 1
    assert metadata["native_metrics"]["Average_i_pTM"]
    assert "Rank" not in metadata["native_metrics"]
    assert "bindcraft_rank" in metadata["native_metrics"]
    assert provenance["adapter_version"] == ADAPTER_VERSION
    assert provenance["command"] == ["fixture-bindcraft"]
    assert provenance["input_hash"] == request_input_hash(_request(1))
    assert "ranking_score" not in payload
    assert "rank" not in payload
    assert "ipsae" not in metadata
    assert "ipsae" not in metadata["native_metrics"]
    assert not hasattr(candidate, "ranking_score")
    assert not hasattr(candidate, "rank")


def test_duplicate_designs_are_rejected(tmp_path):
    stats = _write_stats(
        tmp_path / STATS_FILENAME,
        [
            {
                "Design": "same_design",
                "Sequence": "ACDEFGHIKLMNPQRSTVWY",
            },
            {
                "Design": "same_design",
                "Sequence": "YWVTSRQPNMLKIHGFEDCA",
            },
        ],
    )
    parser = BindCraftParser()
    artifacts = BindCraftArtifacts(
        output_dir=tmp_path,
        stats_csv=stats,
        command=("fixture-bindcraft",),
    )

    with pytest.raises(BindCraftError, match="Duplicate"):
        parser.parse(_request(2), artifacts)


def test_invalid_sequence_is_rejected(tmp_path):
    stats = _write_stats(
        tmp_path / STATS_FILENAME,
        [
            {
                "Design": "bad_seq",
                "Sequence": "ACDE123",
            }
        ],
    )
    parser = BindCraftParser()
    artifacts = BindCraftArtifacts(
        output_dir=tmp_path,
        stats_csv=stats,
        command=("fixture-bindcraft",),
    )

    with pytest.raises(BindCraftError, match="invalid residues"):
        parser.parse(_request(1), artifacts)


def test_process_error_becomes_failed_generation_result(tmp_path):
    script = tmp_path / "failing_bindcraft.py"
    script.write_text("import sys\nsys.exit(2)\n")
    request = _request(
        1,
        constraints={
            "bindcraft_command": [sys.executable, str(script)],
        },
    )
    agent = ProteinGenerationAgent(
        adapters={
            "bindcraft": BindCraftAdapter(
                executor=LocalBindCraftExecutor(),
                work_dir=tmp_path / "work",
            )
        }
    )
    result = agent.generate(request)

    assert result.status == "failed"
    assert result.candidates == ()
    assert result.adapter_name == "bindcraft"
    assert result.error is not None
    assert "return code 2" in result.error


def test_unconfigured_local_executor_fails_without_gpu():
    result = ProteinGenerationAgent().generate(_request())

    assert result.status == "failed"
    assert result.candidates == ()
    assert result.adapter_name == "bindcraft"
    assert result.error is not None
    assert "not configured" in result.error.lower()


def test_local_executor_parses_command_output(tmp_path):
    script = tmp_path / "fake_bindcraft.py"
    script.write_text(
        "from pathlib import Path\n"
        "import shutil\n"
        "import sys\n"
        "shutil.copy(sys.argv[1], Path('final_design_stats.csv'))\n"
    )
    work_dir = tmp_path / "work"
    request = _request(
        3,
        constraints={
            "bindcraft_command": [
                sys.executable,
                str(script),
                str(FIXTURE_DIR / STATS_FILENAME),
            ],
            "bindcraft_version": "fake_local_v1",
        },
    )
    agent = ProteinGenerationAgent(
        adapters={
            "bindcraft": BindCraftAdapter(
                executor=LocalBindCraftExecutor(),
                work_dir=work_dir,
            )
        }
    )
    result = agent.generate(request)

    assert result.status == "complete"
    assert result.adapter_name == "bindcraft"
    assert len(result.candidates) == 3
    assert (
        result.candidates[0].generator_metadata["provenance"][
            "bindcraft_version"
        ]
        == "fake_local_v1"
    )
    assert (work_dir / STATS_FILENAME).is_file()


def test_generation_agent_normalizes_fixture_bindcraft():
    agent = ProteinGenerationAgent(
        adapters={"bindcraft": _adapter()}
    )
    result = agent.generate(_request())

    assert result.status == "complete"
    assert result.adapter_version == ADAPTER_VERSION
    assert len(result.candidates) == 3
    assert result.input_hash == request_input_hash(_request())
    for candidate in result.candidates:
        assert candidate.generator == "bindcraft"
        assert candidate.target_id == "BCMA"


def test_bindcraft_fixture_traverses_supervisor(tmp_path):
    agent = ProteinGenerationAgent(
        adapters={"bindcraft": _adapter(tmp_path / "bindcraft")}
    )
    supervisor = ProteinSupervisorV1(
        request=_request(),
        artifact_dir=tmp_path / "run",
        run_id="protein_run_bindcraft_001",
        generation_agent=agent,
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
    assert generation.adapter_name == "bindcraft"
    assert all(
        candidate.generator == "bindcraft"
        for candidate in generation.candidates
    )
    for record in supervisor.ranking_records:
        assert record.ranking_signal == RANKING_METHOD
        assert record.ranking_score == record.structural_evidence["ipsae"]
        assert record.is_probability is False
    for record in supervisor.critic_records:
        assert (
            record.rank,
            record.ranking_score,
        ) == ranked[record.candidate_id]
    for record in supervisor.portfolio_records:
        assert (
            record.rank,
            record.ranking_score,
        ) == ranked[record.candidate_id]
    native = generation.candidates[0].generator_metadata["native_metrics"]
    assert "Average_i_pTM" in native
    assert run.generation.artifact is not None
    supervisor_source = Path(
        "src/cellertz/domains/protein_design/agents/supervisor.py"
    ).read_text()
    assert "BindCraftAdapter" not in supervisor_source
    assert "from cellertz.domains.protein_design.adapters.bindcraft" not in supervisor_source


def test_parser_accepts_real_gpu3_bindcraft_output():
    fixture_dir = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "bindcraft_gpu3"
    )

    request = GenerationRequest(
        target_id="PDL1",
        objective="binder_design_smoke",
        generator="bindcraft",
        n_candidates=1,
        constraints={
            "length_min": 65,
            "length_max": 65,
        },
    )

    artifacts = BindCraftArtifacts(
        output_dir=fixture_dir,
        stats_csv=fixture_dir / STATS_FILENAME,
        command=("apptainer-bindcraft-gpu3",),
        bindcraft_version=(
            "efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d"
        ),
        returncode=0,
    )

    candidates = BindCraftParser().parse(
        request,
        artifacts,
    )

    assert len(candidates) == 1

    candidate = candidates[0]

    assert candidate.candidate_id == (
        "bindcraft_PDL1_l65_s985772_mpnn1"
    )

    assert candidate.target_id == "PDL1"
    assert candidate.generator == "bindcraft"

    assert candidate.sequence == (
        "SSLFEKIVKWAMEFTVAMKEYMEKNPELTDEQIRTIQKEVM"
        "RRFEAKDFGDPRKLIEEALAKGPP"
    )

    metrics = candidate.generator_metadata[
        "native_metrics"
    ]

    assert metrics["Average_pLDDT"] == "0.83"
    assert metrics["Average_pTM"] == "0.79"
    assert metrics["Average_i_pTM"] == "0.61"
    assert metrics["Average_i_pAE"] == "0.36"
    assert metrics["Average_dG"] == "-40.68"
    assert metrics["Average_dSASA"] == "1794.61"
    assert metrics["Average_ShapeComplementarity"] == "0.68"
    assert metrics["Average_PackStat"] == "0.64"

    provenance = candidate.generator_metadata[
        "provenance"
    ]

    assert provenance["returncode"] == 0
    assert provenance["bindcraft_version"] == (
        "efb5bfeb8b4b1a5944256f979c34e0c8e6a82d9d"
    )
