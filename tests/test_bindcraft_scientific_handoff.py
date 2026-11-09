import hashlib
import inspect
import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from cellertz.domains.protein_design.adapters.bindcraft import (
    STATS_FILENAME,
    BindCraftArtifacts,
    ExistingBindCraftArtifactsExecutor,
)
from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
)
from cellertz.domains.protein_design.agents.supervisor import (
    ProteinSupervisorV1,
)
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    CONTAINER_OUTPUT,
    ApptainerBindCraftGenerator,
    advanced_profile_identity_sha256,
)
from cellertz.domains.protein_design.execution.bindcraft_execution import (
    BindCraftExecutionRequest,
    canonical_bindcraft_generation_request,
    developer_bindcraft_authorization,
    scientific_request_sha256,
)
from cellertz.domains.protein_design.execution.bindcraft_scientific import (
    BINDCRAFT_IPSAE_CONFIG,
    HANDOFF_FILENAME,
    BindCraftHandoffMismatch,
    BindCraftHandoffPersistError,
    persist_bindcraft_execution_handoff,
)
from cellertz.domains.protein_design.execution.bindcraft_snapshot import (
    snapshot_claimed_bindcraft_inputs,
)
from cellertz.mcp import server as mcp_server
from cellertz.mcp import start_run as mcp_start_run
from cellertz.runs.idempotency import FileIdempotencyStore, IdempotencyRecord
from cellertz.runs.starter import (
    LocalProteinRunStarter,
    bindcraft_run_request_hash,
)
from cellertz.runtime import ExecutionRequest, ExecutionResult
from cellertz.state.run_state import FileProteinRunStateStore

FIXTURE_ROOT = Path(__file__).parent / "fixtures"
GPU3_STATS = FIXTURE_ROOT / "bindcraft_gpu3" / "final_design_stats.csv"
GPU4_EVIDENCE = FIXTURE_ROOT / "ipsae_gpu4"
DESIGN = "PDL1_l65_s985772_mpnn1"
CANDIDATE_ID = f"bindcraft_{DESIGN}"
EXECUTION_ID = "exec_pdl1_smoke_gpu4"
RUN_ID = "protein_run_pdl1_gpu4_integration"
GOLDEN_IPSAE = 0.013557


def _materialize_gpu4_designs(designs_dir: Path) -> Path:
    pdb_dir = designs_dir / "MPNN" / "Relaxed"
    pae_dir = designs_dir / "CellertzEvidence" / "PAE"
    pdb_dir.mkdir(parents=True, exist_ok=True)
    pae_dir.mkdir(parents=True, exist_ok=True)
    stats_csv = designs_dir / STATS_FILENAME
    shutil.copyfile(GPU3_STATS, stats_csv)
    for model in (1, 2):
        shutil.copyfile(
            GPU4_EVIDENCE / f"{DESIGN}_model{model}.pdb",
            pdb_dir / f"{DESIGN}_model{model}.pdb",
        )
        shutil.copyfile(
            GPU4_EVIDENCE / f"{DESIGN}_model{model}_pae.npz",
            pae_dir / f"{DESIGN}_model{model}_pae.npz",
        )
    return stats_csv


class FrozenGpu4Runtime:
    name = "frozen-gpu4"
    version = "frozen_gpu4_v1"

    def __init__(self) -> None:
        self.calls = 0
        self.last_request: ExecutionRequest | None = None

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls += 1
        self.last_request = request
        marker = f":{CONTAINER_OUTPUT}"
        for part in request.argv:
            if part.endswith(marker):
                _materialize_gpu4_designs(Path(part[: -len(marker)]))
                break
        return ExecutionResult(
            status="complete",
            return_code=0,
            stdout="",
            stderr="",
            runtime_name=self.name,
            runtime_version=self.version,
            started_at="test-start",
            completed_at="test-end",
        )


class CountingGenerator(ApptainerBindCraftGenerator):
    def __init__(
        self,
        runtime,
        advanced_profile_path: Path | None = None,
    ) -> None:
        super().__init__(
            runtime=runtime,
            advanced_profile_path=advanced_profile_path,
        )
        self.calls = 0
        self.last = None

    def generate(self, request):
        self.calls += 1
        self.last = super().generate(request)
        return self.last


class RecordingSupervisorFactory:
    def __init__(self) -> None:
        self.calls = 0
        self.kwargs: list[dict] = []
        self.supervisors: list[ProteinSupervisorV1] = []

    def __call__(self, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        supervisor = ProteinSupervisorV1(**kwargs)
        self.supervisors.append(supervisor)
        return supervisor


class ExplodingScoreFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1

        def explode(_candidates):
            raise RuntimeError("ipsae scoring exploded")

        return ProteinSupervisorV1(
            **{
                **kwargs,
                "score_fn": explode,
            }
        )


class HoldingSupervisorFactory:
    def __init__(self) -> None:
        self.calls = 0
        self.entered = threading.Event()
        self.hold = threading.Event()
        self.kwargs: list[dict] = []

    def __call__(self, **kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        self.entered.set()
        self.hold.wait(timeout=5)
        return ProteinSupervisorV1(**kwargs)


class ObservingStore(FileIdempotencyStore):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.wait_started = threading.Event()

    def wait_until_settled(self, idempotency_key: str):
        self.wait_started.set()
        return super().wait_until_settled(idempotency_key)


class CountingSupervisorFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, **_kwargs):
        self.calls += 1
        raise AssertionError("Supervisor must not be created.")


def _safe_profile(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "design_algorithm": "4stage",
                "max_trajectories": 1,
                "num_seqs": 1,
                "max_mpnn_sequences": 1,
            }
        ),
        encoding="utf-8",
    )
    return path


def _starter(
    tmp_path: Path,
    supervisor_factory,
    store=None,
) -> LocalProteinRunStarter:
    return LocalProteinRunStarter(
        idempotency_store=store
        or FileIdempotencyStore(tmp_path / "idempotency"),
        run_state_store=FileProteinRunStateStore(tmp_path / "runs"),
        artifact_root=tmp_path / "artifacts",
        supervisor_factory=supervisor_factory,
    )


def _ready_request(
    tmp_path: Path,
    *,
    run_id: str = RUN_ID,
    execution_id: str = EXECUTION_ID,
    seed: int = 7,
) -> BindCraftExecutionRequest:
    target = tmp_path / "target.pdb"
    settings = tmp_path / "settings.json"
    apptainer = tmp_path / "apptainer"
    container = tmp_path / "bindcraft.sif"
    target.write_text("ATOM\n", encoding="utf-8")
    settings.write_text("{}\n", encoding="utf-8")
    apptainer.write_text("fake-apptainer\n", encoding="utf-8")
    container.write_bytes(b"fake-sif")
    return BindCraftExecutionRequest(
        generation=GenerationRequest(
            target_id="PDL1",
            objective="binder_design",
            generator="bindcraft",
            n_candidates=1,
            constraints={"length_min": 65, "length_max": 65},
        ),
        run_id=run_id,
        execution_id=execution_id,
        target_path=target,
        output_root=tmp_path / "scientific_execution",
        apptainer_bin=apptainer,
        settings_path=settings,
        container_image=str(container),
        container_digest=hashlib.sha256(b"fake-sif").hexdigest(),
        tool_version="bindcraft-test",
        seed=seed,
        gpu_required=True,
    )


def _scientific_digest(generation: GenerationRequest) -> str:
    return scientific_request_sha256(
        canonical_bindcraft_generation_request(generation)
    )


def _golden_run(tmp_path: Path):
    supervisor_factory = RecordingSupervisorFactory()
    runtime = FrozenGpu4Runtime()
    profile = _safe_profile(tmp_path / "profile.json")
    generator = CountingGenerator(runtime, profile)
    starter = _starter(tmp_path, supervisor_factory)
    request = _ready_request(tmp_path)
    result = starter.start_bindcraft_run(
        request,
        "bindcraft-scientific-golden",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    return result, starter, generator, runtime, supervisor_factory, request


def test_successful_execution_hands_exact_artifacts_to_science(tmp_path):
    result, _, generator, runtime, factory, _ = _golden_run(tmp_path)

    assert result.execution_status == "complete"
    assert result.scientific_status == "complete"
    assert result.status == "complete"
    assert result.status != "validated"
    assert generator.calls == 1
    assert runtime.calls == 1
    assert factory.calls == 1
    assert generator.last is not None
    assert generator.last.artifacts is not None

    agent = factory.kwargs[0]["generation_agent"]
    executor = agent._adapters["bindcraft"].executor
    assert isinstance(executor, ExistingBindCraftArtifactsExecutor)
    produced = generator.last.artifacts
    reused = executor.artifacts
    assert reused.stats_csv.resolve() == produced.stats_csv.resolve()
    assert reused.execution_provenance["execution_id"] == (
        produced.execution_provenance["execution_id"]
    )
    assert reused.execution_provenance["run_id"] == (
        produced.execution_provenance["run_id"]
    )
    assert reused.execution_provenance["manifest_sha256"] == (
        produced.execution_provenance["manifest_sha256"]
    )
    assert executor.execute(
        factory.kwargs[0]["request"],
        tmp_path / "unused",
    ) is reused


def test_bindcraft_executes_exactly_once_on_success_and_replay(tmp_path):
    result, starter, generator, runtime, factory, request = _golden_run(
        tmp_path
    )
    replay = starter.start_bindcraft_run(
        request,
        "bindcraft-scientific-golden",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.status == "complete"
    assert replay.idempotent_replay is True
    assert replay.status == "complete"
    assert replay.execution_status == "complete"
    assert replay.scientific_status == "complete"
    assert generator.calls == 1
    assert runtime.calls == 1
    assert factory.calls == 1


def test_candidate_provenance_preserves_execution_provenance(tmp_path):
    _, _, generator, _, factory, request = _golden_run(tmp_path)
    supervisor = factory.supervisors[0]
    assert supervisor.generation_result is not None
    candidate = supervisor.generation_result.candidates[0]
    execution = candidate.generator_metadata["provenance"]["execution"]
    produced = generator.last.artifacts.execution_provenance
    claimed_sha = advanced_profile_identity_sha256(
        tmp_path / "profile.json"
    )

    assert execution["run_id"] == request.run_id
    assert execution["execution_id"] == request.execution_id
    assert execution["manifest_sha256"] == produced["manifest_sha256"]
    assert execution["container_digest"] == request.container_digest
    assert execution["execution_container_image"] == (
        produced["execution_container_image"]
    )
    assert execution["tool_version"] == "bindcraft-test"
    assert execution["seed"] == 7
    assert execution["advanced_profile_sha256"] == claimed_sha
    assert Path(execution["execution_container_image"]).is_absolute()
    assert "scientific identity" not in json.dumps(execution)


def test_source_profile_mutation_after_snapshot_keeps_claimed_sha(
    tmp_path,
    monkeypatch,
):
    factory = RecordingSupervisorFactory()
    runtime = FrozenGpu4Runtime()
    profile = _safe_profile(tmp_path / "profile.json")
    generator = CountingGenerator(runtime, profile)
    starter = _starter(tmp_path, factory)
    request = _ready_request(tmp_path)
    sha_a = advanced_profile_identity_sha256(profile)

    def snapshot_then_mutate_source(*args, **kwargs):
        snapshot = snapshot_claimed_bindcraft_inputs(*args, **kwargs)
        profile.write_text(
            json.dumps(
                {
                    "design_algorithm": "mutated_after_snapshot",
                    "max_trajectories": 1,
                    "num_seqs": 1,
                    "max_mpnn_sequences": 1,
                }
            ),
            encoding="utf-8",
        )
        return snapshot

    monkeypatch.setattr(
        "cellertz.runs.starter.snapshot_claimed_bindcraft_inputs",
        snapshot_then_mutate_source,
    )

    result = starter.start_bindcraft_run(
        request,
        "bindcraft-profile-snapshot-sha",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    sha_b = advanced_profile_identity_sha256(profile)
    assert sha_a != sha_b
    assert result.status == "complete"
    assert result.execution_status == "complete"
    assert result.scientific_status == "complete"
    assert generator.calls == 1
    assert runtime.calls == 1
    assert factory.calls == 1
    assert generator.last is not None
    assert generator.last.artifacts is not None
    assert generator.last.plan.advanced_sha256 == sha_a
    produced = generator.last.artifacts.execution_provenance
    assert produced["advanced_profile_sha256"] == sha_a
    handoff = json.loads(
        (
            request.output_root / EXECUTION_ID / HANDOFF_FILENAME
        ).read_text(encoding="utf-8")
    )
    assert handoff["execution_provenance"]["advanced_profile_sha256"] == (
        sha_a
    )
    supervisor = factory.supervisors[0]
    assert supervisor.generation_result is not None
    candidate = supervisor.generation_result.candidates[0]
    provenance = candidate.generator_metadata["provenance"]["execution"]
    assert provenance["advanced_profile_sha256"] == sha_a
    score = supervisor.score_results[0]
    assert score.ranking_score == pytest.approx(GOLDEN_IPSAE, abs=5e-7)
    assert generator.effective_advanced_profile_sha256() == sha_b
    record = starter.idempotency_store.get("bindcraft-profile-snapshot-sha")
    assert record is not None
    assert record.scientific_request_sha256 == _scientific_digest(
        request.generation
    )
    assert handoff["scientific_request_sha256"] == (
        record.scientific_request_sha256
    )


def test_frozen_gpu4_ipsae_and_supervisor_ranking(tmp_path):
    result, starter, _, _, factory, _ = _golden_run(tmp_path)
    supervisor = factory.supervisors[0]
    assert result.scientific_status == "complete"
    assert len(supervisor.score_results) == 1
    score = supervisor.score_results[0]
    assert score.candidate_id == CANDIDATE_ID
    assert score.ranking_score == pytest.approx(GOLDEN_IPSAE, abs=5e-7)
    assert score.structural_evidence["ipsae"] == pytest.approx(
        GOLDEN_IPSAE,
        abs=5e-7,
    )

    evidence = score.provenance["ipsae_evidence"]
    assert evidence["model_aggregation"] == "mean"
    assert evidence["n_models"] == 2
    assert evidence["ipsae_mean"] == pytest.approx(GOLDEN_IPSAE, abs=5e-7)
    assert evidence["ipsae_min"] == pytest.approx(0.010173, abs=5e-7)
    assert evidence["ipsae_max"] == pytest.approx(0.016941, abs=5e-7)
    assert evidence["protocol_parameters"] == {
        "pae_cutoff": BINDCRAFT_IPSAE_CONFIG.pae_cutoff,
        "distance_cutoff": BINDCRAFT_IPSAE_CONFIG.distance_cutoff,
        "chain_a": BINDCRAFT_IPSAE_CONFIG.chain_a,
        "chain_b": BINDCRAFT_IPSAE_CONFIG.chain_b,
        "within_model_aggregation": BINDCRAFT_IPSAE_CONFIG.aggregation,
    }
    assert evidence["models"][0]["pdb_sha256"]
    assert evidence["models"][1]["pdb_sha256"]
    assert evidence["models"][0]["pae_sha256"] == (
        "dbf52bfd7910b49e5c87ec8569965be9"
        "a56ad53d4c65497584afbebd34b4e2a2"
    )
    assert evidence["models"][1]["pae_sha256"] == (
        "7b14c58cee82945883adc1e5e5e17e13"
        "dd11a7672b52d9decfa5467a510ce9a2"
    )
    assert evidence["is_probability"] is False
    assert evidence["recommended_as_hard_filter"] is False

    assert len(supervisor.ranking_records) == 1
    ranked = supervisor.ranking_records[0]
    assert ranked.candidate_id == CANDIDATE_ID
    assert ranked.rank == 1
    assert ranked.ranking_score == pytest.approx(GOLDEN_IPSAE, abs=5e-7)

    persisted = json.loads(
        (
            starter.artifact_root / RUN_ID / "scoring.json"
        ).read_text(encoding="utf-8")
    )
    assert persisted[0]["ranking_score"] == pytest.approx(
        GOLDEN_IPSAE,
        abs=5e-7,
    )


def test_execution_failure_does_not_call_supervisor(tmp_path):
    factory = CountingSupervisorFactory()
    runtime = FrozenGpu4Runtime()
    runtime.execute = lambda request: (_ for _ in ()).throw(
        RuntimeError("bindcraft runtime exploded")
    )
    generator = CountingGenerator(runtime)
    starter = _starter(tmp_path, factory)
    result = starter.start_bindcraft_run(
        _ready_request(tmp_path),
        "bindcraft-scientific-exec-fail",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    assert result.execution_status == "failed"
    assert result.scientific_status is None
    assert result.status == "failed"
    assert factory.calls == 0
    assert generator.calls == 1


def test_missing_physical_artifact_does_not_call_supervisor(tmp_path):
    class EmptyCompleteRuntime:
        name = "empty"
        version = "empty_v1"
        calls = 0

        def execute(self, request: ExecutionRequest) -> ExecutionResult:
            self.calls += 1
            return ExecutionResult(
                status="complete",
                return_code=0,
                stdout="",
                stderr="",
                runtime_name=self.name,
                runtime_version=self.version,
                started_at="test-start",
                completed_at="test-end",
            )

    factory = CountingSupervisorFactory()
    runtime = EmptyCompleteRuntime()
    generator = CountingGenerator(runtime)
    starter = _starter(tmp_path, factory)
    result = starter.start_bindcraft_run(
        _ready_request(tmp_path),
        "bindcraft-scientific-missing-stats",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    assert result.execution_status == "failed"
    assert result.scientific_status is None
    assert result.status == "failed"
    assert result.error == "bindcraft_stats_missing"
    assert factory.calls == 0
    assert generator.calls == 1


def test_scientific_failure_preserves_execution_and_does_not_rerun(
    tmp_path,
):
    factory = ExplodingScoreFactory()
    runtime = FrozenGpu4Runtime()
    generator = CountingGenerator(
        runtime,
        _safe_profile(tmp_path / "profile.json"),
    )
    starter = _starter(tmp_path, factory)
    request = _ready_request(tmp_path)
    first = starter.start_bindcraft_run(
        request,
        "bindcraft-scientific-score-fail",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    second = starter.start_bindcraft_run(
        request,
        "bindcraft-scientific-score-fail",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert first.execution_status == "complete"
    assert first.scientific_status == "failed"
    assert first.status == "failed"
    assert "ipsae scoring exploded" in (first.error or "")
    assert second.idempotent_replay is True
    assert second.execution_status == "complete"
    assert second.scientific_status == "failed"
    assert generator.calls == 1
    assert runtime.calls == 1
    assert factory.calls == 1
    assert (
        request.output_root / EXECUTION_ID / "designs" / STATS_FILENAME
    ).is_file()


def test_execution_complete_resumes_science_without_bindcraft(tmp_path):
    factory = RecordingSupervisorFactory()
    runtime = FrozenGpu4Runtime()
    profile = _safe_profile(tmp_path / "profile.json")
    generator = CountingGenerator(runtime, profile)
    starter = _starter(tmp_path, factory)
    request = _ready_request(tmp_path)
    designs_dir = request.output_root / EXECUTION_ID / "designs"
    stats_csv = _materialize_gpu4_designs(designs_dir)
    artifacts = BindCraftArtifacts(
        output_dir=designs_dir,
        stats_csv=stats_csv,
        command=("frozen-resume",),
        bindcraft_version="bindcraft-test",
        returncode=0,
        execution_provenance={
            "execution_id": EXECUTION_ID,
            "run_id": RUN_ID,
            "manifest_sha256": "a" * 64,
            "container_digest": request.container_digest,
            "execution_container_image": "/tmp/snapshot.sif",
            "tool_version": "bindcraft-test",
            "seed": 7,
            "advanced_profile_sha256": (
                generator.effective_advanced_profile_sha256()
            ),
        },
    )
    persist_bindcraft_execution_handoff(
        request.output_root / EXECUTION_ID,
        artifacts,
        expected_provenance={
            "execution_id": EXECUTION_ID,
            "run_id": RUN_ID,
            "manifest_sha256": "a" * 64,
            "container_digest": request.container_digest,
            "tool_version": "bindcraft-test",
            "seed": 7,
            "advanced_profile_sha256": (
                generator.effective_advanced_profile_sha256()
            ),
        },
        request_hash=bindcraft_run_request_hash(
            request,
            advanced_profile_sha256=(
                generator.effective_advanced_profile_sha256()
            ),
        ),
        scientific_request=request.generation,
    )
    starter.idempotency_store.try_claim(
        IdempotencyRecord(
            idempotency_key="bindcraft-scientific-resume",
            request_hash=bindcraft_run_request_hash(
                request,
                advanced_profile_sha256=(
                    generator.effective_advanced_profile_sha256()
                ),
            ),
            run_id=request.run_id,
            mode="real",
            created_at="2026-01-01T00:00:00+00:00",
            status="execution_complete",
            execution_id=EXECUTION_ID,
            output_dir=str(request.output_root / EXECUTION_ID),
            manifest_sha256="a" * 64,
            execution_status="complete",
            scientific_request_sha256=_scientific_digest(request.generation),
        )
    )

    result = starter.start_bindcraft_run(
        request,
        "bindcraft-scientific-resume",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=False,
    )

    assert result.idempotent_replay is True
    assert result.execution_status == "complete"
    assert result.scientific_status == "complete"
    assert result.status == "complete"
    assert generator.calls == 0
    assert runtime.calls == 0
    assert factory.calls == 1
    score = factory.supervisors[0].score_results[0]
    assert score.ranking_score == pytest.approx(GOLDEN_IPSAE, abs=5e-7)


def test_concurrent_arrival_during_science_runs_supervisor_once(tmp_path):
    factory = HoldingSupervisorFactory()
    store = ObservingStore(tmp_path / "idempotency")
    runtime = FrozenGpu4Runtime()
    generator = CountingGenerator(
        runtime,
        _safe_profile(tmp_path / "profile.json"),
    )
    starter = _starter(tmp_path, factory, store=store)
    request = _ready_request(tmp_path)

    def first():
        return starter.start_bindcraft_run(
            request,
            "bindcraft-scientific-concurrent",
            generator=generator,
            authorization=developer_bindcraft_authorization(),
            gpu_available=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first)
        assert factory.entered.wait(timeout=5)
        second_future = pool.submit(
            starter.start_bindcraft_run,
            request,
            "bindcraft-scientific-concurrent",
            generator=generator,
            authorization=developer_bindcraft_authorization(),
            gpu_available=True,
        )
        assert store.wait_started.wait(timeout=5)
        assert factory.calls == 1
        factory.hold.set()
        first_result = first_future.result(timeout=10)
        second_result = second_future.result(timeout=10)

    assert first_result.status == "complete"
    assert second_result.idempotent_replay is True
    assert second_result.status == "complete"
    assert second_result.run_id == first_result.run_id
    assert factory.calls == 1
    assert generator.calls == 1
    assert runtime.calls == 1


def test_replay_uses_original_execution_locator(tmp_path):
    result, starter, generator, runtime, factory, request = _golden_run(
        tmp_path
    )
    replay_request = replace(
        request,
        run_id="protein_run_replay_attempt",
        execution_id="exec_replay_attempt",
        output_root=tmp_path / "other-output",
    )
    replay = starter.start_bindcraft_run(
        replay_request,
        "bindcraft-scientific-golden",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.status == "complete"
    assert replay.idempotent_replay is True
    assert replay.run_id == RUN_ID
    assert replay.run_id != replay_request.run_id
    assert factory.calls == 1
    assert factory.kwargs[0]["run_id"] == RUN_ID
    assert generator.calls == 1
    assert runtime.calls == 1
    original_stats = (
        tmp_path / "scientific_execution" / EXECUTION_ID / "designs"
        / STATS_FILENAME
    )
    agent = factory.kwargs[0]["generation_agent"]
    artifacts = agent._adapters["bindcraft"].executor.artifacts
    assert artifacts.stats_csv.resolve() == original_stats.resolve()
    assert artifacts.execution_provenance["run_id"] == RUN_ID
    assert artifacts.execution_provenance["execution_id"] == EXECUTION_ID
    assert not (tmp_path / "other-output").exists() or not list(
        (tmp_path / "other-output").glob("**/final_design_stats.csv")
    )
    persisted = json.loads(
        (starter.artifact_root / RUN_ID / "scoring.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted[0]["ranking_score"] == pytest.approx(
        GOLDEN_IPSAE,
        abs=5e-7,
    )
    assert not (starter.artifact_root / "protein_run_replay_attempt").exists()


def test_scientific_pending_is_not_automatically_taken_over(tmp_path):
    factory = CountingSupervisorFactory()
    store = FileIdempotencyStore(
        tmp_path / "idempotency",
        settle_timeout_s=0,
    )
    runtime = FrozenGpu4Runtime()
    generator = CountingGenerator(
        runtime,
        _safe_profile(tmp_path / "profile.json"),
    )
    starter = _starter(tmp_path, factory, store=store)
    request = _ready_request(tmp_path)
    request_hash = bindcraft_run_request_hash(
        request,
        advanced_profile_sha256=generator.effective_advanced_profile_sha256(),
    )
    store.try_claim(
        IdempotencyRecord(
            idempotency_key="bindcraft-scientific-pending",
            request_hash=request_hash,
            run_id=RUN_ID,
            mode="real",
            created_at="2026-01-01T00:00:00+00:00",
            status="scientific_pending",
            execution_id=EXECUTION_ID,
            output_dir=str(request.output_root / EXECUTION_ID),
            manifest_sha256="a" * 64,
            execution_status="complete",
            scientific_status="pending",
        )
    )

    result = starter.start_bindcraft_run(
        request,
        "bindcraft-scientific-pending",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.idempotent_replay is True
    assert result.status == "scientific_pending"
    assert result.execution_status == "complete"
    assert result.scientific_status == "pending"
    assert result.run_id == RUN_ID
    assert factory.calls == 0
    assert generator.calls == 0


def test_malformed_handoff_fails_closed_without_supervisor(tmp_path):
    _assert_handoff_fails_closed(
        tmp_path,
        "bindcraft-handoff-truncated",
        write_handoff=lambda path, request_hash: path.write_text(
            "{",
            encoding="utf-8",
        ),
        expected_error="bindcraft_handoff_malformed",
    )


def test_wrong_execution_id_handoff_fails_closed(tmp_path):
    _assert_stale_handoff_fails_closed(
        tmp_path,
        "bindcraft-handoff-wrong-exec",
        mutate={"execution_id": "exec_other"},
        expected_error="bindcraft_handoff_execution_id_mismatch",
    )


def test_wrong_run_id_handoff_fails_closed(tmp_path):
    _assert_stale_handoff_fails_closed(
        tmp_path,
        "bindcraft-handoff-wrong-run",
        mutate={"run_id": "protein_run_other"},
        expected_error="bindcraft_handoff_run_id_mismatch",
    )


def test_wrong_manifest_sha_handoff_fails_closed(tmp_path):
    _assert_stale_handoff_fails_closed(
        tmp_path,
        "bindcraft-handoff-wrong-manifest",
        mutate={"manifest_sha256": "b" * 64},
        expected_error="bindcraft_handoff_manifest_sha256_mismatch",
    )


def test_missing_provenance_handoff_fails_closed(tmp_path):
    _assert_stale_handoff_fails_closed(
        tmp_path,
        "bindcraft-handoff-missing-provenance",
        mutate={"execution_provenance": {}},
        expected_error="bindcraft_handoff_provenance_missing",
    )


def test_replay_layered_status_does_not_guess_from_filesystem(tmp_path):
    factory = ExplodingScoreFactory()
    runtime = FrozenGpu4Runtime()
    generator = CountingGenerator(
        runtime,
        _safe_profile(tmp_path / "profile.json"),
    )
    starter = _starter(tmp_path, factory)
    request = _ready_request(tmp_path)
    first = starter.start_bindcraft_run(
        request,
        "bindcraft-scientific-layered",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    handoff = (
        request.output_root / EXECUTION_ID / HANDOFF_FILENAME
    )
    assert first.execution_status == "complete"
    assert first.scientific_status == "failed"
    handoff.unlink()
    second = starter.start_bindcraft_run(
        request,
        "bindcraft-scientific-layered",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    assert second.idempotent_replay is True
    assert second.execution_status == "complete"
    assert second.scientific_status == "failed"
    assert second.status == "failed"
    assert generator.calls == 1
    assert factory.calls == 1
    record = starter.idempotency_store.get("bindcraft-scientific-layered")
    assert record is not None
    assert record.execution_status == "complete"
    assert record.scientific_status == "failed"


def test_physical_failure_layered_status_is_distinct(tmp_path):
    factory = CountingSupervisorFactory()
    runtime = FrozenGpu4Runtime()
    runtime.execute = lambda request: (_ for _ in ()).throw(
        RuntimeError("bindcraft runtime exploded")
    )
    generator = CountingGenerator(runtime)
    starter = _starter(tmp_path, factory)
    request = _ready_request(tmp_path)
    first = starter.start_bindcraft_run(
        request,
        "bindcraft-scientific-physical-fail",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    second = starter.start_bindcraft_run(
        request,
        "bindcraft-scientific-physical-fail",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    record = starter.idempotency_store.get(
        "bindcraft-scientific-physical-fail"
    )
    assert first.execution_status == "failed"
    assert first.scientific_status is None
    assert second.execution_status == "failed"
    assert second.scientific_status is None
    assert record is not None
    assert record.execution_status == "failed"
    assert record.scientific_status is None
    assert factory.calls == 0


def _assert_handoff_fails_closed(
    tmp_path: Path,
    key: str,
    *,
    write_handoff,
    expected_error: str,
) -> None:
    factory = CountingSupervisorFactory()
    runtime = FrozenGpu4Runtime()
    generator = CountingGenerator(
        runtime,
        _safe_profile(tmp_path / "profile.json"),
    )
    starter = _starter(tmp_path, factory)
    request = _ready_request(tmp_path)
    output_dir = request.output_root / EXECUTION_ID
    output_dir.mkdir(parents=True)
    request_hash = bindcraft_run_request_hash(
        request,
        advanced_profile_sha256=generator.effective_advanced_profile_sha256(),
    )
    write_handoff(output_dir / HANDOFF_FILENAME, request_hash)
    starter.idempotency_store.try_claim(
        IdempotencyRecord(
            idempotency_key=key,
            request_hash=request_hash,
            run_id=RUN_ID,
            mode="real",
            created_at="2026-01-01T00:00:00+00:00",
            status="execution_complete",
            execution_id=EXECUTION_ID,
            output_dir=str(output_dir),
            manifest_sha256="a" * 64,
            execution_status="complete",
            scientific_request_sha256=_scientific_digest(request.generation),
        )
    )
    result = starter.start_bindcraft_run(
        request,
        key,
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    assert result.status == "failed"
    assert result.execution_status == "complete"
    assert result.scientific_status == "failed"
    assert expected_error in (result.error or "")
    assert factory.calls == 0
    assert generator.calls == 0
    record = starter.idempotency_store.get(key)
    assert record is not None
    assert record.status == "failed"
    assert record.status not in {"pending", "scientific_pending"}
    assert record.execution_status == "complete"
    assert record.scientific_status == "failed"
    return starter


def _assert_stale_handoff_fails_closed(
    tmp_path: Path,
    key: str,
    *,
    mutate: dict,
    expected_error: str,
) -> None:
    def write_handoff(path: Path, request_hash: str) -> None:
        designs = path.parent / "designs"
        stats = _materialize_gpu4_designs(designs)
        artifacts = BindCraftArtifacts(
            output_dir=designs,
            stats_csv=stats,
            command=("frozen",),
            bindcraft_version="bindcraft-test",
            returncode=0,
            execution_provenance={
                "execution_id": EXECUTION_ID,
                "run_id": RUN_ID,
                "manifest_sha256": "a" * 64,
            },
        )
        persist_bindcraft_execution_handoff(
            path.parent,
            artifacts,
            expected_provenance={
                "execution_id": EXECUTION_ID,
                "run_id": RUN_ID,
                "manifest_sha256": "a" * 64,
            },
            request_hash=request_hash,
            scientific_request=GenerationRequest(
                target_id="PDL1",
                objective="binder_design",
                generator="bindcraft",
                n_candidates=1,
                constraints={"length_min": 65, "length_max": 65},
            ),
        )
        document = json.loads(path.read_text(encoding="utf-8"))
        document.update(mutate)
        path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    _assert_handoff_fails_closed(
        tmp_path,
        key,
        write_handoff=write_handoff,
        expected_error=expected_error,
    )


def test_completed_replay_skips_current_preflight(tmp_path):
    result, starter, generator, runtime, factory, request = _golden_run(
        tmp_path
    )
    assert request.apptainer_bin is not None
    request.apptainer_bin.unlink()
    replay = starter.start_bindcraft_run(
        request,
        "bindcraft-scientific-golden",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=False,
    )
    assert result.status == "complete"
    assert replay.status == "complete"
    assert replay.idempotent_replay is True
    assert replay.run_id == result.run_id
    assert generator.calls == 1
    assert runtime.calls == 1
    assert factory.calls == 1


def test_completed_replay_does_not_create_new_output_root(tmp_path):
    result, starter, generator, _, factory, request = _golden_run(tmp_path)
    other_root = tmp_path / "other-output"
    replay = starter.start_bindcraft_run(
        replace(request, output_root=other_root),
        "bindcraft-scientific-golden",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=False,
    )
    assert result.status == "complete"
    assert replay.status == "complete"
    assert replay.run_id == RUN_ID
    assert generator.calls == 1
    assert factory.calls == 1
    assert not other_root.exists()


def test_resume_rejects_injected_replay_fields(tmp_path):
    factory = RecordingSupervisorFactory()
    runtime = FrozenGpu4Runtime()
    profile = _safe_profile(tmp_path / "profile.json")
    generator = CountingGenerator(runtime, profile)
    starter = _starter(tmp_path, factory)
    request = _ready_request(tmp_path)
    designs_dir = request.output_root / EXECUTION_ID / "designs"
    stats_csv = _materialize_gpu4_designs(designs_dir)
    artifacts = BindCraftArtifacts(
        output_dir=designs_dir,
        stats_csv=stats_csv,
        command=("frozen-resume",),
        bindcraft_version="bindcraft-test",
        returncode=0,
        execution_provenance={
            "execution_id": EXECUTION_ID,
            "run_id": RUN_ID,
            "manifest_sha256": "a" * 64,
            "container_digest": request.container_digest,
            "tool_version": "bindcraft-test",
            "seed": 7,
            "advanced_profile_sha256": (
                generator.effective_advanced_profile_sha256()
            ),
        },
    )
    persist_bindcraft_execution_handoff(
        request.output_root / EXECUTION_ID,
        artifacts,
        expected_provenance={
            "execution_id": EXECUTION_ID,
            "run_id": RUN_ID,
            "manifest_sha256": "a" * 64,
            "container_digest": request.container_digest,
            "tool_version": "bindcraft-test",
            "seed": 7,
            "advanced_profile_sha256": (
                generator.effective_advanced_profile_sha256()
            ),
        },
        request_hash=bindcraft_run_request_hash(
            request,
            advanced_profile_sha256=(
                generator.effective_advanced_profile_sha256()
            ),
        ),
        scientific_request=request.generation,
    )
    starter.idempotency_store.try_claim(
        IdempotencyRecord(
            idempotency_key="bindcraft-scientific-canonical",
            request_hash=bindcraft_run_request_hash(
                request,
                advanced_profile_sha256=(
                    generator.effective_advanced_profile_sha256()
                ),
            ),
            run_id=request.run_id,
            mode="real",
            created_at="2026-01-01T00:00:00+00:00",
            status="execution_complete",
            execution_id=EXECUTION_ID,
            output_dir=str(request.output_root / EXECUTION_ID),
            manifest_sha256="a" * 64,
            execution_status="complete",
            scientific_request_sha256=_scientific_digest(request.generation),
        )
    )
    poisoned = replace(
        request,
        generation=replace(
            request.generation,
            constraints={
                **request.generation.constraints,
                "ipsae": 0.99,
                "ranking_score": 1.0,
                "rank": 1,
                "selected": True,
                "critic": "override",
            },
        ),
    )
    result = starter.start_bindcraft_run(
        poisoned,
        "bindcraft-scientific-canonical",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=False,
    )
    assert result.status == "complete"
    assert generator.calls == 0
    assert factory.calls == 1
    received = factory.kwargs[0]["request"]
    original = canonical_bindcraft_generation_request(request.generation)
    assert received == original
    assert "ipsae" not in received.constraints
    assert "rank" not in received.constraints
    assert "selected" not in received.constraints
    assert "critic" not in received.constraints
    score = factory.supervisors[0].score_results[0]
    assert score.ranking_score == pytest.approx(GOLDEN_IPSAE, abs=5e-7)


def test_persist_rejects_mismatched_execution_provenance(tmp_path):
    request = _ready_request(tmp_path)
    designs_dir = tmp_path / "designs"
    stats_csv = _materialize_gpu4_designs(designs_dir)
    artifacts = BindCraftArtifacts(
        output_dir=designs_dir,
        stats_csv=stats_csv,
        command=("frozen",),
        bindcraft_version="bindcraft-test",
        returncode=0,
        execution_provenance={
            "execution_id": "exec_other",
            "run_id": RUN_ID,
            "manifest_sha256": "a" * 64,
        },
    )
    output_dir = tmp_path / "handoff-out"
    with pytest.raises(BindCraftHandoffMismatch, match="execution_id"):
        persist_bindcraft_execution_handoff(
            output_dir,
            artifacts,
            expected_provenance={
                "execution_id": EXECUTION_ID,
                "run_id": RUN_ID,
                "manifest_sha256": "a" * 64,
            },
            request_hash="hash",
            scientific_request=request.generation,
        )
    assert not (output_dir / HANDOFF_FILENAME).exists()

    artifacts = BindCraftArtifacts(
        output_dir=designs_dir,
        stats_csv=stats_csv,
        command=("frozen",),
        bindcraft_version="bindcraft-test",
        returncode=0,
        execution_provenance={
            "execution_id": EXECUTION_ID,
            "run_id": "protein_run_other",
            "manifest_sha256": "a" * 64,
        },
    )
    with pytest.raises(BindCraftHandoffMismatch, match="run_id"):
        persist_bindcraft_execution_handoff(
            output_dir,
            artifacts,
            expected_provenance={
                "execution_id": EXECUTION_ID,
                "run_id": RUN_ID,
                "manifest_sha256": "a" * 64,
            },
            request_hash="hash",
            scientific_request=request.generation,
        )

    artifacts = BindCraftArtifacts(
        output_dir=designs_dir,
        stats_csv=stats_csv,
        command=("frozen",),
        bindcraft_version="bindcraft-test",
        returncode=0,
        execution_provenance={
            "execution_id": EXECUTION_ID,
            "run_id": RUN_ID,
            "manifest_sha256": "b" * 64,
        },
    )
    with pytest.raises(BindCraftHandoffMismatch, match="manifest_sha256"):
        persist_bindcraft_execution_handoff(
            output_dir,
            artifacts,
            expected_provenance={
                "execution_id": EXECUTION_ID,
                "run_id": RUN_ID,
                "manifest_sha256": "a" * 64,
            },
            request_hash="hash",
            scientific_request=request.generation,
        )
    assert not (output_dir / HANDOFF_FILENAME).exists()


def test_handoff_persist_failure_settles_scientific_failed(
    tmp_path,
    monkeypatch,
):
    def boom(*_args, **_kwargs):
        raise BindCraftHandoffPersistError("bindcraft_handoff_persist_failed")

    monkeypatch.setattr(
        "cellertz.runs.starter.persist_bindcraft_execution_handoff",
        boom,
    )
    factory = CountingSupervisorFactory()
    runtime = FrozenGpu4Runtime()
    generator = CountingGenerator(
        runtime,
        _safe_profile(tmp_path / "profile.json"),
    )
    starter = _starter(tmp_path, factory)
    result = starter.start_bindcraft_run(
        _ready_request(tmp_path),
        "bindcraft-handoff-persist-fail",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    record = starter.idempotency_store.get("bindcraft-handoff-persist-fail")
    assert result.status == "failed"
    assert result.execution_status == "complete"
    assert result.scientific_status == "failed"
    assert result.error == "bindcraft_handoff_persist_failed"
    assert record is not None
    assert record.status == "failed"
    assert record.execution_status == "complete"
    assert record.scientific_status == "failed"
    assert record.status not in {"pending", "scientific_pending"}
    assert generator.calls == 1
    assert factory.calls == 0


def test_missing_locator_persists_terminal_failure(tmp_path):
    factory = CountingSupervisorFactory()
    generator = CountingGenerator(
        FrozenGpu4Runtime(),
        _safe_profile(tmp_path / "profile.json"),
    )
    starter = _starter(tmp_path, factory)
    request = _ready_request(tmp_path)
    starter.idempotency_store.try_claim(
        IdempotencyRecord(
            idempotency_key="bindcraft-missing-locator",
            request_hash=bindcraft_run_request_hash(
                request,
                advanced_profile_sha256=(
                    generator.effective_advanced_profile_sha256()
                ),
            ),
            run_id=RUN_ID,
            mode="real",
            created_at="2026-01-01T00:00:00+00:00",
            status="execution_complete",
            execution_status="complete",
        )
    )
    result = starter.start_bindcraft_run(
        request,
        "bindcraft-missing-locator",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=False,
    )
    record = starter.idempotency_store.get("bindcraft-missing-locator")
    assert result.status == "failed"
    assert result.error == "bindcraft_execution_locator_missing"
    assert record is not None
    assert record.status == "failed"
    assert record.execution_status == "complete"
    assert record.scientific_status == "failed"
    assert factory.calls == 0
    assert generator.calls == 0


def test_malformed_returncode_fails_closed_without_leaving_pending(tmp_path):
    def write_handoff(path: Path, request_hash: str) -> None:
        _materialize_gpu4_designs(path.parent / "designs")
        path.write_text(
            json.dumps(
                {
                    "command": ["frozen"],
                    "execution_id": EXECUTION_ID,
                    "execution_provenance": {
                        "execution_id": EXECUTION_ID,
                        "manifest_sha256": "a" * 64,
                        "run_id": RUN_ID,
                    },
                    "manifest_sha256": "a" * 64,
                    "request_hash": request_hash,
                    "returncode": "0",
                    "run_id": RUN_ID,
                    "scientific_request": {
                        "constraints": {"length_min": 65, "length_max": 65},
                        "generator": "bindcraft",
                        "n_candidates": 1,
                        "objective": "binder_design",
                        "target_id": "PDL1",
                    },
                    "stats_csv": STATS_FILENAME,
                }
            ),
            encoding="utf-8",
        )

    _assert_handoff_fails_closed(
        tmp_path,
        "bindcraft-handoff-returncode",
        write_handoff=write_handoff,
        expected_error="bindcraft_handoff_malformed",
    )


def _canonical_scientific_document(**changes: object) -> dict:
    document = {
        "constraints": {"length_min": 65, "length_max": 65},
        "generator": "bindcraft",
        "n_candidates": 1,
        "objective": "binder_design",
        "target_id": "PDL1",
    }
    document.update(changes)
    return document


def test_modified_scientific_request_body_fails_closed(tmp_path):
    _assert_stale_handoff_fails_closed(
        tmp_path,
        "bindcraft-handoff-scientific-body",
        mutate={"scientific_request": _canonical_scientific_document(
            target_id="OTHER",
        )},
        expected_error="bindcraft_handoff_scientific_request_sha256_mismatch",
    )


def test_modified_n_candidates_fails_closed(tmp_path):
    _assert_stale_handoff_fails_closed(
        tmp_path,
        "bindcraft-handoff-n-candidates",
        mutate={"scientific_request": _canonical_scientific_document(
            n_candidates=2,
        )},
        expected_error="bindcraft_handoff_scientific_request_sha256_mismatch",
    )


def test_modified_objective_fails_closed(tmp_path):
    _assert_stale_handoff_fails_closed(
        tmp_path,
        "bindcraft-handoff-objective",
        mutate={"scientific_request": _canonical_scientific_document(
            objective="other_objective",
        )},
        expected_error="bindcraft_handoff_scientific_request_sha256_mismatch",
    )


def test_modified_constraints_fail_closed(tmp_path):
    _assert_stale_handoff_fails_closed(
        tmp_path,
        "bindcraft-handoff-constraints",
        mutate={"scientific_request": _canonical_scientific_document(
            constraints={"length_min": 80, "length_max": 80},
        )},
        expected_error="bindcraft_handoff_scientific_request_sha256_mismatch",
    )


def test_handoff_cannot_rewrite_body_and_local_digest(tmp_path):
    def write_handoff(path: Path, request_hash: str) -> None:
        designs = path.parent / "designs"
        stats = _materialize_gpu4_designs(designs)
        artifacts = BindCraftArtifacts(
            output_dir=designs,
            stats_csv=stats,
            command=("frozen",),
            bindcraft_version="bindcraft-test",
            returncode=0,
            execution_provenance={
                "execution_id": EXECUTION_ID,
                "run_id": RUN_ID,
                "manifest_sha256": "a" * 64,
            },
        )
        persist_bindcraft_execution_handoff(
            path.parent,
            artifacts,
            expected_provenance={
                "execution_id": EXECUTION_ID,
                "run_id": RUN_ID,
                "manifest_sha256": "a" * 64,
            },
            request_hash=request_hash,
            scientific_request=GenerationRequest(
                target_id="PDL1",
                objective="binder_design",
                generator="bindcraft",
                n_candidates=1,
                constraints={"length_min": 65, "length_max": 65},
            ),
        )
        document = json.loads(path.read_text(encoding="utf-8"))
        document["scientific_request"] = _canonical_scientific_document(
            n_candidates=2,
        )
        document["scientific_request_sha256"] = scientific_request_sha256(
            GenerationRequest(
                target_id="PDL1",
                objective="binder_design",
                generator="bindcraft",
                n_candidates=2,
                constraints={"length_min": 65, "length_max": 65},
            )
        )
        path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    _assert_handoff_fails_closed(
        tmp_path,
        "bindcraft-handoff-rewritten-digest",
        write_handoff=write_handoff,
        expected_error="bindcraft_handoff_scientific_request_sha256_mismatch",
    )


def test_malformed_target_id_type_fails_closed(tmp_path):
    _assert_stale_handoff_fails_closed(
        tmp_path,
        "bindcraft-handoff-target-type",
        mutate={"scientific_request": _canonical_scientific_document(
            target_id=123,
        )},
        expected_error="bindcraft_handoff_malformed",
    )


def test_malformed_objective_type_fails_closed(tmp_path):
    _assert_stale_handoff_fails_closed(
        tmp_path,
        "bindcraft-handoff-objective-type",
        mutate={"scientific_request": _canonical_scientific_document(
            objective=["binder_design"],
        )},
        expected_error="bindcraft_handoff_malformed",
    )


def test_malformed_n_candidates_bool_fails_closed(tmp_path):
    _assert_stale_handoff_fails_closed(
        tmp_path,
        "bindcraft-handoff-n-candidates-bool",
        mutate={"scientific_request": _canonical_scientific_document(
            n_candidates=True,
        )},
        expected_error="bindcraft_handoff_malformed",
    )


def test_execution_complete_without_scientific_digest_fails_closed(tmp_path):
    factory = CountingSupervisorFactory()
    runtime = FrozenGpu4Runtime()
    generator = CountingGenerator(
        runtime,
        _safe_profile(tmp_path / "profile.json"),
    )
    starter = _starter(tmp_path, factory)
    request = _ready_request(tmp_path)
    designs_dir = request.output_root / EXECUTION_ID / "designs"
    stats_csv = _materialize_gpu4_designs(designs_dir)
    artifacts = BindCraftArtifacts(
        output_dir=designs_dir,
        stats_csv=stats_csv,
        command=("frozen-resume",),
        bindcraft_version="bindcraft-test",
        returncode=0,
        execution_provenance={
            "execution_id": EXECUTION_ID,
            "run_id": RUN_ID,
            "manifest_sha256": "a" * 64,
            "container_digest": request.container_digest,
            "tool_version": "bindcraft-test",
            "seed": 7,
            "advanced_profile_sha256": (
                generator.effective_advanced_profile_sha256()
            ),
        },
    )
    persist_bindcraft_execution_handoff(
        request.output_root / EXECUTION_ID,
        artifacts,
        expected_provenance={
            "execution_id": EXECUTION_ID,
            "run_id": RUN_ID,
            "manifest_sha256": "a" * 64,
            "container_digest": request.container_digest,
            "tool_version": "bindcraft-test",
            "seed": 7,
            "advanced_profile_sha256": (
                generator.effective_advanced_profile_sha256()
            ),
        },
        request_hash=bindcraft_run_request_hash(
            request,
            advanced_profile_sha256=(
                generator.effective_advanced_profile_sha256()
            ),
        ),
        scientific_request=request.generation,
    )
    starter.idempotency_store.try_claim(
        IdempotencyRecord(
            idempotency_key="bindcraft-missing-scientific-digest",
            request_hash=bindcraft_run_request_hash(
                request,
                advanced_profile_sha256=(
                    generator.effective_advanced_profile_sha256()
                ),
            ),
            run_id=request.run_id,
            mode="real",
            created_at="2026-01-01T00:00:00+00:00",
            status="execution_complete",
            execution_id=EXECUTION_ID,
            output_dir=str(request.output_root / EXECUTION_ID),
            manifest_sha256="a" * 64,
            execution_status="complete",
        )
    )
    result = starter.start_bindcraft_run(
        request,
        "bindcraft-missing-scientific-digest",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=False,
    )
    record = starter.idempotency_store.get(
        "bindcraft-missing-scientific-digest"
    )
    assert result.status == "failed"
    assert result.error == "bindcraft_scientific_request_integrity_missing"
    assert record is not None
    assert record.status == "failed"
    assert record.execution_status == "complete"
    assert record.scientific_status == "failed"
    assert factory.calls == 0
    assert generator.calls == 0


def test_ipsae_is_not_labeled_probability_or_hard_filter():
    from cellertz.domains.protein_design.execution import (
        bindcraft_scientific,
    )
    from cellertz.runs import starter as starter_module

    for source in (
        inspect.getsource(bindcraft_scientific),
        inspect.getsource(
            starter_module.LocalProteinRunStarter.start_bindcraft_run
        ),
        inspect.getsource(
            starter_module.LocalProteinRunStarter._run_bindcraft_science
        ),
    ):
        lowered = source.lower()
        assert "probability" not in lowered
        assert "hard filter" not in lowered
        assert "hard_filter" not in lowered


def test_mcp_start_run_remains_fixture_only():
    start_source = inspect.getsource(mcp_start_run)
    server_source = inspect.getsource(mcp_server)
    assert "start_bindcraft_run" not in start_source
    assert "start_bindcraft_run" not in server_source
    assert "protein_start_fixture_run" in start_source
    assert "enable_generation=True" not in start_source
    assert "enable_generation=True" not in server_source
