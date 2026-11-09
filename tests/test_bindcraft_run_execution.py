import hashlib
import inspect
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cellertz.domains.protein_design.adapters.bindcraft import (
    STATS_FILENAME,
)
from cellertz.domains.protein_design.agents.generation import (
    GenerationRequest,
)
from cellertz.domains.protein_design.execution.bindcraft_apptainer import (
    CONTAINER_OUTPUT,
    ApptainerBindCraftGenerator,
    advanced_profile_identity_sha256,
)
from cellertz.domains.protein_design.execution.bindcraft_execution import (
    BindCraftExecutionRequest,
    developer_bindcraft_authorization,
    file_sha256,
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


def _materialize_required_stats(argv: tuple[str, ...]) -> None:
    marker = f":{CONTAINER_OUTPUT}"
    for part in argv:
        if part.endswith(marker):
            host = Path(part[: -len(marker)])
            host.mkdir(parents=True, exist_ok=True)
            (host / STATS_FILENAME).write_text(
                "Design,Sequence\n",
                encoding="utf-8",
            )
            return


class RecordingRuntime:
    name = "recording"
    version = "recording_v1"

    def __init__(
        self,
        *,
        status: str = "complete",
        write_stats: bool = True,
    ) -> None:
        self.calls = 0
        self.last_request = None
        self.status = status
        self.write_stats = write_stats

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls += 1
        self.last_request = request
        if self.status == "complete" and self.write_stats:
            _materialize_required_stats(request.argv)
        return ExecutionResult(
            status=self.status,
            return_code=0 if self.status == "complete" else 2,
            stdout="fake stdout",
            stderr="" if self.status == "complete" else "fake failure",
            runtime_name=self.name,
            runtime_version=self.version,
            started_at="test-start",
            completed_at="test-end",
            error=None if self.status == "complete" else "fake failure",
        )


class ExplodingRuntime(RecordingRuntime):
    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls += 1
        self.last_request = request
        raise RuntimeError("bindcraft runtime exploded")


class HoldingRuntime(RecordingRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.hold = threading.Event()

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.entered.set()
        self.hold.wait(timeout=5)
        return super().execute(request)


class ExecutionCountingGenerator(ApptainerBindCraftGenerator):
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


class CountingSupervisorFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, **_kwargs):
        self.calls += 1
        raise AssertionError(
            "Supervisor must not be created on the BindCraft path."
        )


def _safe_profile(path: Path, **overrides) -> Path:
    payload = {
        "design_algorithm": "4stage",
        "max_trajectories": 1,
        "num_seqs": 1,
        "max_mpnn_sequences": 1,
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _starter(tmp_path: Path, supervisor_factory) -> LocalProteinRunStarter:
    return LocalProteinRunStarter(
        idempotency_store=FileIdempotencyStore(tmp_path / "idempotency"),
        run_state_store=FileProteinRunStateStore(tmp_path / "runs"),
        artifact_root=tmp_path / "artifacts",
        supervisor_factory=supervisor_factory,
    )


def _ready_request(
    tmp_path: Path,
    *,
    run_id: str = "protein_run_ready_bindcraft",
    execution_id: str | None = "exec_ready_bindcraft",
    seed: int = 7,
    checkpoint: bool = False,
) -> BindCraftExecutionRequest:
    target = tmp_path / "target.pdb"
    settings = tmp_path / "settings.json"
    apptainer = tmp_path / "apptainer"
    container = tmp_path / "bindcraft.sif"
    if not target.exists():
        target.write_text("ATOM\n", encoding="utf-8")
    if not settings.exists():
        settings.write_text("{}\n", encoding="utf-8")
    if not apptainer.exists():
        apptainer.write_text("fake-apptainer\n", encoding="utf-8")
    container_bytes = b"fake-sif"
    if not container.exists():
        container.write_bytes(container_bytes)
    checkpoint_path = None
    if checkpoint:
        checkpoint_path = tmp_path / "checkpoint.bin"
        if not checkpoint_path.exists():
            checkpoint_path.write_bytes(b"checkpoint-v1")
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
        checkpoint_path=checkpoint_path,
        container_image=str(container),
        container_digest=hashlib.sha256(container_bytes).hexdigest(),
        tool_version="bindcraft-test",
        seed=seed,
        gpu_required=True,
    )


def _record_document(tmp_path: Path) -> dict[str, object]:
    records = list((tmp_path / "idempotency").glob("*.json"))
    assert len(records) == 1
    return json.loads(records[0].read_text(encoding="utf-8"))


def test_authorized_ready_fresh_key_reaches_generator_once(tmp_path):
    supervisor_factory = CountingSupervisorFactory()
    runtime = RecordingRuntime()
    profile = _safe_profile(tmp_path / "profile.json")
    generator = ExecutionCountingGenerator(runtime, profile)
    starter = _starter(tmp_path, supervisor_factory)
    request = _ready_request(tmp_path)

    result = starter.start_bindcraft_run(
        request,
        "bindcraft-execute-key",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.execution_status == "complete"
    assert result.scientific_status == "failed"
    assert result.status == "failed"
    assert result.created is True
    assert result.idempotent_replay is False
    assert result.run_id == request.run_id
    assert result.status != "validated"
    assert generator.calls == 1
    assert runtime.calls == 1
    assert supervisor_factory.calls == 1
    assert _record_document(tmp_path)["status"] == "failed"
    assert _record_document(tmp_path)["status"] != "validated"


def test_same_key_same_science_replays_without_second_generate(tmp_path):
    supervisor_factory = CountingSupervisorFactory()
    runtime = RecordingRuntime()
    generator = ExecutionCountingGenerator(runtime)
    starter = _starter(tmp_path, supervisor_factory)
    request = _ready_request(tmp_path)

    first = starter.start_bindcraft_run(
        request,
        "bindcraft-replay-exec",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    second = starter.start_bindcraft_run(
        request,
        "bindcraft-replay-exec",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert first.execution_status == "complete"
    assert first.scientific_status == "failed"
    assert first.status == "failed"
    assert second.idempotent_replay is True
    assert second.created is False
    assert second.status == "failed"
    assert second.execution_status == "complete"
    assert second.run_id == first.run_id
    assert generator.calls == 1
    assert runtime.calls == 1
    assert supervisor_factory.calls == 1


def test_same_key_different_science_conflicts_without_generate(tmp_path):
    supervisor_factory = CountingSupervisorFactory()
    runtime = RecordingRuntime()
    generator = ExecutionCountingGenerator(runtime)
    starter = _starter(tmp_path, supervisor_factory)

    first = starter.start_bindcraft_run(
        _ready_request(tmp_path, seed=7),
        "bindcraft-conflict-exec",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    second = starter.start_bindcraft_run(
        _ready_request(tmp_path, seed=8),
        "bindcraft-conflict-exec",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert first.execution_status == "complete"
    assert first.status == "failed"
    assert second.status == "conflict"
    assert second.error == "idempotency_key_conflict"
    assert generator.calls == 1
    assert runtime.calls == 1
    assert supervisor_factory.calls == 1


def test_concurrent_same_key_executes_once(tmp_path):
    supervisor_factory = CountingSupervisorFactory()
    runtime = HoldingRuntime()
    generator = ExecutionCountingGenerator(runtime)
    starter = _starter(tmp_path, supervisor_factory)
    request = _ready_request(tmp_path)
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        return starter.start_bindcraft_run(
            request,
            "bindcraft-concurrent-exec",
            generator=generator,
            authorization=developer_bindcraft_authorization(),
            gpu_available=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(go), pool.submit(go)]
        assert runtime.entered.wait(timeout=5)
        runtime.hold.set()
        results = [future.result(timeout=10) for future in futures]

    assert {item.idempotent_replay for item in results} == {False, True}
    assert {item.execution_status for item in results} == {"complete"}
    assert generator.calls == 1
    assert runtime.calls == 1
    assert supervisor_factory.calls == 1


def test_mutated_target_after_claim_fails_closed(tmp_path, monkeypatch):
    supervisor_factory = CountingSupervisorFactory()
    runtime = RecordingRuntime()
    generator = ExecutionCountingGenerator(runtime)
    starter = _starter(tmp_path, supervisor_factory)
    request = _ready_request(tmp_path)

    def mutate_then_snapshot(*args, **kwargs):
        assert request.target_path is not None
        request.target_path.write_text("ATOM\nMUTATED\n", encoding="utf-8")
        return snapshot_claimed_bindcraft_inputs(*args, **kwargs)

    monkeypatch.setattr(
        "cellertz.runs.starter.snapshot_claimed_bindcraft_inputs",
        mutate_then_snapshot,
    )

    result = starter.start_bindcraft_run(
        request,
        "bindcraft-drift-target",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.status == "failed"
    assert result.error == "bindcraft_input_drift"
    assert result.created is True
    assert generator.calls == 0
    assert runtime.calls == 0
    assert supervisor_factory.calls == 0
    assert _record_document(tmp_path)["status"] == "failed"


def test_mutated_settings_after_claim_fails_closed(tmp_path, monkeypatch):
    runtime = RecordingRuntime()
    generator = ExecutionCountingGenerator(runtime)
    starter = _starter(tmp_path, CountingSupervisorFactory())
    request = _ready_request(tmp_path)

    def mutate_then_snapshot(*args, **kwargs):
        assert request.settings_path is not None
        request.settings_path.write_text('{"x":1}\n', encoding="utf-8")
        return snapshot_claimed_bindcraft_inputs(*args, **kwargs)

    monkeypatch.setattr(
        "cellertz.runs.starter.snapshot_claimed_bindcraft_inputs",
        mutate_then_snapshot,
    )
    result = starter.start_bindcraft_run(
        request,
        "bindcraft-drift-settings",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    assert result.error == "bindcraft_input_drift"
    assert generator.calls == 0
    assert runtime.calls == 0


def test_mutated_checkpoint_after_claim_fails_closed(tmp_path, monkeypatch):
    runtime = RecordingRuntime()
    generator = ExecutionCountingGenerator(runtime)
    starter = _starter(tmp_path, CountingSupervisorFactory())
    request = _ready_request(tmp_path, checkpoint=True)

    def mutate_then_snapshot(*args, **kwargs):
        assert request.checkpoint_path is not None
        request.checkpoint_path.write_bytes(b"checkpoint-v2")
        return snapshot_claimed_bindcraft_inputs(*args, **kwargs)

    monkeypatch.setattr(
        "cellertz.runs.starter.snapshot_claimed_bindcraft_inputs",
        mutate_then_snapshot,
    )
    result = starter.start_bindcraft_run(
        request,
        "bindcraft-drift-checkpoint",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    assert result.error == "bindcraft_input_drift"
    assert runtime.calls == 0


def test_mutated_advanced_profile_after_claim_fails_closed(
    tmp_path,
    monkeypatch,
):
    runtime = RecordingRuntime()
    profile = _safe_profile(tmp_path / "profile.json")
    generator = ExecutionCountingGenerator(runtime, profile)
    starter = _starter(tmp_path, CountingSupervisorFactory())
    request = _ready_request(tmp_path)

    def mutate_then_snapshot(*args, **kwargs):
        _safe_profile(profile, extra="after-claim")
        return snapshot_claimed_bindcraft_inputs(*args, **kwargs)

    monkeypatch.setattr(
        "cellertz.runs.starter.snapshot_claimed_bindcraft_inputs",
        mutate_then_snapshot,
    )
    result = starter.start_bindcraft_run(
        request,
        "bindcraft-drift-profile",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    assert result.error == "bindcraft_input_drift"
    assert runtime.calls == 0


def test_container_digest_mismatch_fails_closed(tmp_path, monkeypatch):
    runtime = RecordingRuntime()
    generator = ExecutionCountingGenerator(runtime)
    starter = _starter(tmp_path, CountingSupervisorFactory())
    request = _ready_request(tmp_path)

    def mutate_then_snapshot(*args, **kwargs):
        Path(request.container_image).write_bytes(b"other-sif")
        return snapshot_claimed_bindcraft_inputs(*args, **kwargs)

    monkeypatch.setattr(
        "cellertz.runs.starter.snapshot_claimed_bindcraft_inputs",
        mutate_then_snapshot,
    )
    result = starter.start_bindcraft_run(
        request,
        "bindcraft-drift-container",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    assert result.status == "failed"
    assert result.error == "bindcraft_container_digest_mismatch"
    assert runtime.calls == 0


def test_runtime_failure_persists_failed_state(tmp_path):
    supervisor_factory = CountingSupervisorFactory()
    runtime = RecordingRuntime(status="failed")
    generator = ExecutionCountingGenerator(runtime)
    starter = _starter(tmp_path, supervisor_factory)

    result = starter.start_bindcraft_run(
        _ready_request(tmp_path),
        "bindcraft-runtime-failed",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.status == "failed"
    assert result.execution_status == "failed"
    assert result.scientific_status is None
    assert result.created is True
    assert result.error == "fake failure"
    assert generator.last is not None
    assert generator.last.artifacts is None
    assert _record_document(tmp_path)["status"] == "failed"
    assert _record_document(tmp_path)["status"] != "validated"
    assert supervisor_factory.calls == 0


def test_runtime_exception_persists_failed_state(tmp_path):
    supervisor_factory = CountingSupervisorFactory()
    runtime = ExplodingRuntime()
    generator = ExecutionCountingGenerator(runtime)
    starter = _starter(tmp_path, supervisor_factory)

    result = starter.start_bindcraft_run(
        _ready_request(tmp_path),
        "bindcraft-runtime-exception",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.status == "failed"
    assert result.execution_status == "failed"
    assert result.scientific_status is None
    assert result.error == "bindcraft runtime exploded"
    assert _record_document(tmp_path)["status"] == "failed"
    assert _record_document(tmp_path)["status"] != "pending"
    assert supervisor_factory.calls == 0


def test_successful_fake_runtime_preserves_execution_provenance(tmp_path):
    runtime = RecordingRuntime()
    profile = _safe_profile(tmp_path / "profile.json")
    generator = ExecutionCountingGenerator(runtime, profile)
    starter = _starter(tmp_path, CountingSupervisorFactory())
    request = _ready_request(tmp_path, run_id="protein_run_prov")

    result = starter.start_bindcraft_run(
        request,
        "bindcraft-provenance",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.execution_status == "complete"
    assert result.status != "validated"
    assert generator.last is not None
    assert generator.last.artifacts is not None
    provenance = generator.last.artifacts.execution_provenance
    claimed_sha = advanced_profile_identity_sha256(profile)
    assert generator.last.plan.advanced_sha256 == claimed_sha
    assert provenance["advanced_profile_sha256"] == claimed_sha
    assert provenance["run_id"] == "protein_run_prov"
    assert provenance["execution_id"] == request.execution_id
    assert provenance["container_digest"] == request.container_digest
    assert provenance["execution_container_image"] != (
        request.container_image
    )
    assert provenance["execution_container_image"] in (
        generator.last.plan.argv
    )
    assert request.container_image not in generator.last.plan.argv
    assert provenance["tool_version"] == "bindcraft-test"
    assert provenance["seed"] == 7
    assert result.status != "validated"
    assert generator.last.artifacts.stats_csv.is_file()
    assert generator.last.artifacts.stats_csv.resolve().is_relative_to(
        generator.last.artifacts.output_dir.resolve()
    )
    assert generator.last.artifacts.output_dir == (
        generator.last.plan.bindcraft_output_dir
    )


def test_runtime_consumes_snapshot_container_not_source(
    tmp_path,
    monkeypatch,
):
    runtime = RecordingRuntime()
    generator = ExecutionCountingGenerator(runtime)
    starter = _starter(tmp_path, CountingSupervisorFactory())
    request = _ready_request(tmp_path)
    source = Path(request.container_image)
    claimed_digest = request.container_digest

    def snapshot_then_mutate_source(*args, **kwargs):
        snapshot = snapshot_claimed_bindcraft_inputs(*args, **kwargs)
        source.write_bytes(b"other-sif")
        return snapshot

    monkeypatch.setattr(
        "cellertz.runs.starter.snapshot_claimed_bindcraft_inputs",
        snapshot_then_mutate_source,
    )

    result = starter.start_bindcraft_run(
        request,
        "bindcraft-container-snapshot",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.execution_status == "complete"
    executed = Path(
        generator.last.artifacts.execution_provenance[
            "execution_container_image"
        ]
    )
    assert executed.resolve() != source.resolve()
    assert str(executed) in generator.last.plan.argv
    assert str(source) not in generator.last.plan.argv
    assert file_sha256(executed) == claimed_digest
    assert file_sha256(source) != claimed_digest
    assert runtime.calls == 1


def test_runtime_complete_without_stats_is_lifecycle_failed(tmp_path):
    supervisor_factory = CountingSupervisorFactory()
    runtime = RecordingRuntime(write_stats=False)
    generator = ExecutionCountingGenerator(runtime)
    starter = _starter(tmp_path, supervisor_factory)

    result = starter.start_bindcraft_run(
        _ready_request(tmp_path),
        "bindcraft-missing-stats",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.status == "failed"
    assert result.execution_status == "failed"
    assert result.scientific_status is None
    assert result.error == "bindcraft_stats_missing"
    assert result.created is True
    assert result.status != "validated"
    assert _record_document(tmp_path)["status"] == "failed"
    assert generator.calls == 1
    assert runtime.calls == 1
    assert supervisor_factory.calls == 0
    assert generator.last.artifacts is not None
    assert not generator.last.artifacts.stats_csv.is_file()


def test_historical_rejected_key_is_not_reopened(tmp_path):
    supervisor_factory = CountingSupervisorFactory()
    runtime = RecordingRuntime()
    generator = ExecutionCountingGenerator(runtime)
    starter = _starter(tmp_path, supervisor_factory)
    request = _ready_request(tmp_path)
    request_hash = bindcraft_run_request_hash(
        request,
        advanced_profile_sha256=generator.effective_advanced_profile_sha256(),
    )
    starter.idempotency_store.try_claim(
        IdempotencyRecord(
            idempotency_key="bindcraft-historical-rejected",
            request_hash=request_hash,
            run_id=request.run_id,
            mode="real",
            created_at="2026-01-01T00:00:00+00:00",
            status="rejected",
        )
    )

    result = starter.start_bindcraft_run(
        request,
        "bindcraft-historical-rejected",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.idempotent_replay is True
    assert result.status == "rejected"
    assert result.created is False
    assert generator.calls == 0
    assert runtime.calls == 0
    assert supervisor_factory.calls == 0


def test_mcp_start_run_remains_fixture_only():
    start_source = inspect.getsource(mcp_start_run)
    server_source = inspect.getsource(mcp_server)
    assert "start_bindcraft_run" not in start_source
    assert "start_bindcraft_run" not in server_source
    assert "protein_start_fixture_run" in start_source
    assert "enable_generation=True" not in start_source
    assert "enable_generation=True" not in server_source
