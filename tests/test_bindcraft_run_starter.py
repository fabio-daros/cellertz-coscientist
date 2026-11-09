import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
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
)
from cellertz.runs.idempotency import FileIdempotencyStore, IdempotencyRecord
from cellertz.runs.starter import (
    LocalProteinRunStarter,
    bindcraft_run_request_hash,
)
from cellertz.runtime import ExecutionRequest, ExecutionResult
from cellertz.state.run_state import FileProteinRunStateStore


class RecordingRuntime:
    name = "recording"
    version = "recording_v1"

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls += 1
        marker = f":{CONTAINER_OUTPUT}"
        for part in request.argv:
            if part.endswith(marker):
                host = Path(part[: -len(marker)])
                host.mkdir(parents=True, exist_ok=True)
                (host / STATS_FILENAME).write_text(
                    "Design,Sequence\n",
                    encoding="utf-8",
                )
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


class HoldingRuntime(RecordingRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.hold = threading.Event()

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.entered.set()
        self.hold.wait(timeout=5)
        return super().execute(request)


class CountingGenerator(ApptainerBindCraftGenerator):
    def __init__(
        self,
        advanced_profile_path: Path | None = None,
        *,
        runtime=None,
    ) -> None:
        super().__init__(
            runtime=runtime or RecordingRuntime(),
            advanced_profile_path=advanced_profile_path,
        )
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        return super().generate(request)


class BlockingGenerator(ApptainerBindCraftGenerator):
    def __init__(
        self,
        advanced_profile_path: Path | None = None,
    ) -> None:
        super().__init__(advanced_profile_path=advanced_profile_path)
        self.calls = 0

    def generate(self, _request):
        self.calls += 1
        raise AssertionError(
            "BindCraft generation must not run on this path."
        )


class CountingSupervisorFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, **_kwargs):
        self.calls += 1
        raise AssertionError(
            "Supervisor must not be created on the BindCraft path."
        )


def _starter(
    tmp_path: Path,
    supervisor_factory,
) -> LocalProteinRunStarter:
    return LocalProteinRunStarter(
        idempotency_store=FileIdempotencyStore(
            tmp_path / "idempotency"
        ),
        run_state_store=FileProteinRunStateStore(
            tmp_path / "runs"
        ),
        artifact_root=tmp_path / "artifacts",
        supervisor_factory=supervisor_factory,
    )


def _safe_profile(path: Path, **overrides) -> Path:
    payload = {
        "design_algorithm": "4stage",
        "max_trajectories": 1,
        "num_seqs": 1,
        "max_mpnn_sequences": 1,
    }
    payload.update(overrides)
    path.write_text(
        json.dumps(payload, indent=None, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _request() -> BindCraftExecutionRequest:
    return BindCraftExecutionRequest(
        generation=GenerationRequest(
            target_id="PDL1",
            objective="binder_design",
            generator="bindcraft",
            n_candidates=1,
            constraints={
                "length_min": 65,
                "length_max": 65,
            },
        ),
        run_id="protein_run_blocked_bindcraft",
    )


def _ready_request(
    tmp_path: Path,
    *,
    run_id: str = "protein_run_ready_bindcraft",
    execution_id: str | None = "exec_ready_bindcraft",
    seed: int = 7,
) -> BindCraftExecutionRequest:
    target = tmp_path / "target.pdb"
    settings = tmp_path / "settings.json"
    apptainer = tmp_path / "apptainer"
    container = tmp_path / "bindcraft.sif"

    if not target.exists():
        target.write_text(
            "ATOM\n",
            encoding="utf-8",
        )
    if not settings.exists():
        settings.write_text(
            "{}\n",
            encoding="utf-8",
        )
    if not apptainer.exists():
        apptainer.write_text(
            "fake-apptainer\n",
            encoding="utf-8",
        )

    container_bytes = b"fake-sif"
    if not container.exists():
        container.write_bytes(container_bytes)

    return BindCraftExecutionRequest(
        generation=GenerationRequest(
            target_id="PDL1",
            objective="binder_design",
            generator="bindcraft",
            n_candidates=1,
            constraints={
                "length_min": 65,
                "length_max": 65,
            },
        ),
        run_id=run_id,
        execution_id=execution_id,
        target_path=target,
        output_root=tmp_path / "scientific_execution",
        apptainer_bin=apptainer,
        settings_path=settings,
        container_image=str(container),
        container_digest=hashlib.sha256(
            container_bytes
        ).hexdigest(),
        tool_version="bindcraft-test",
        seed=seed,
        gpu_required=True,
    )


def _idempotency_records(tmp_path: Path) -> list[Path]:
    root = tmp_path / "idempotency"
    if not root.exists():
        return []
    return list(root.glob("*.json"))


def _record_document(tmp_path: Path) -> dict[str, object]:
    records = _idempotency_records(tmp_path)
    assert len(records) == 1
    return json.loads(records[0].read_text(encoding="utf-8"))


def test_bindcraft_run_defaults_fail_closed_without_real_authorization(
    tmp_path,
):
    supervisor_factory = CountingSupervisorFactory()
    generator = BlockingGenerator()

    starter = _starter(
        tmp_path,
        supervisor_factory,
    )

    result = starter.start_bindcraft_run(
        _request(),
        "bindcraft-blocked-key",
        generator=generator,
        gpu_available=True,
    )

    assert result.mode == "real"
    assert result.status == "rejected"
    assert result.created is False
    assert result.idempotent_replay is False
    assert result.run_id is None
    assert result.error is not None

    assert generator.calls == 0
    assert supervisor_factory.calls == 0

    assert _idempotency_records(tmp_path) == []
    assert not list(
        (tmp_path / "runs").glob("**/state.json")
    )


def test_authorized_bindcraft_run_is_still_compute_closed(
    tmp_path,
):
    supervisor_factory = CountingSupervisorFactory()
    generator = BlockingGenerator()
    starter = _starter(tmp_path, supervisor_factory)
    request = _ready_request(tmp_path)
    starter.idempotency_store.try_claim(
        IdempotencyRecord(
            idempotency_key="bindcraft-authorized-but-closed",
            request_hash=bindcraft_run_request_hash(
                request,
                advanced_profile_sha256=(
                    generator.effective_advanced_profile_sha256()
                ),
            ),
            run_id=request.run_id,
            mode="real",
            created_at="2026-01-01T00:00:00+00:00",
            status="rejected",
        )
    )

    result = starter.start_bindcraft_run(
        request,
        "bindcraft-authorized-but-closed",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert result.mode == "real"
    assert result.status == "rejected"
    assert result.created is False
    assert result.idempotent_replay is True
    assert result.error is None

    assert generator.calls == 0
    assert supervisor_factory.calls == 0

    document = _record_document(tmp_path)
    assert document["status"] == "rejected"
    assert document["status"] not in {
        "complete",
        "generated",
        "validated",
        "pending",
    }
    assert not list(
        (tmp_path / "runs").glob("**/state.json")
    )


def test_authorized_bindcraft_run_stops_when_preflight_is_not_ready(
    tmp_path,
):
    supervisor_factory = CountingSupervisorFactory()
    generator = BlockingGenerator()

    starter = _starter(
        tmp_path,
        supervisor_factory,
    )

    result = starter.start_bindcraft_run(
        _ready_request(tmp_path),
        "bindcraft-preflight-not-ready",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=False,
    )

    assert result.mode == "real"
    assert result.status == "rejected"
    assert result.created is False
    assert result.idempotent_replay is False
    assert result.run_id is None
    assert result.error == "bindcraft_preflight_not_ready"

    assert generator.calls == 0
    assert supervisor_factory.calls == 0

    assert _idempotency_records(tmp_path) == []
    assert not list(
        (tmp_path / "runs").glob("**/state.json")
    )


def test_ready_authorized_first_claim_is_replayed_not_reexecuted(
    tmp_path,
):
    supervisor_factory = CountingSupervisorFactory()
    generator = CountingGenerator()
    starter = _starter(tmp_path, supervisor_factory)
    request = _ready_request(tmp_path)

    first = starter.start_bindcraft_run(
        request,
        "bindcraft-replay-key",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    second = starter.start_bindcraft_run(
        request,
        "bindcraft-replay-key",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert first.execution_status == "complete"
    assert first.scientific_status == "failed"
    assert first.status == "failed"
    assert first.idempotent_replay is False
    assert second.created is False
    assert second.idempotent_replay is True
    assert second.status == first.status
    assert second.execution_status == "complete"
    assert generator.calls == 1
    assert supervisor_factory.calls == 1
    assert len(_idempotency_records(tmp_path)) == 1


def test_rejected_not_implemented_key_is_terminal(
    tmp_path,
):
    supervisor_factory = CountingSupervisorFactory()
    profile = _safe_profile(tmp_path / "profile.json")
    generator = BlockingGenerator(profile)
    starter = _starter(tmp_path, supervisor_factory)
    request = _ready_request(tmp_path)
    claimed_profile_sha = generator.effective_advanced_profile_sha256()
    claimed_hash = bindcraft_run_request_hash(
        request,
        advanced_profile_sha256=claimed_profile_sha,
    )
    starter.idempotency_store.try_claim(
        IdempotencyRecord(
            idempotency_key="bindcraft-terminal-key",
            request_hash=claimed_hash,
            run_id=request.run_id,
            mode="real",
            created_at="2026-01-01T00:00:00+00:00",
            status="rejected",
        )
    )

    replay = starter.start_bindcraft_run(
        request,
        "bindcraft-terminal-key",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert replay.idempotent_replay is True
    assert replay.status == "rejected"
    assert replay.created is False

    claimed = _record_document(tmp_path)
    assert claimed["status"] == "rejected"
    assert claimed["request_hash"] == claimed_hash

    profile.write_text(
        json.dumps(
            {
                "design_algorithm": "4stage",
                "max_trajectories": 1,
                "num_seqs": 1,
                "max_mpnn_sequences": 1,
                "extra": "after-claim",
            }
        ),
        encoding="utf-8",
    )
    live_profile_sha = advanced_profile_identity_sha256(
        profile
    )
    assert live_profile_sha != claimed_profile_sha
    assert _record_document(tmp_path)["request_hash"] == (
        claimed_hash
    )

    mutated = starter.start_bindcraft_run(
        request,
        "bindcraft-terminal-key",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert mutated.status == "conflict"
    assert mutated.error == "idempotency_key_conflict"
    assert mutated.created is False
    assert mutated.idempotent_replay is False
    assert generator.calls == 0
    assert supervisor_factory.calls == 0
    assert len(_idempotency_records(tmp_path)) == 1


def test_same_key_different_science_conflicts(tmp_path):
    supervisor_factory = CountingSupervisorFactory()
    generator = CountingGenerator()
    starter = _starter(tmp_path, supervisor_factory)

    first = starter.start_bindcraft_run(
        _ready_request(tmp_path, seed=7),
        "bindcraft-conflict-key",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    second = starter.start_bindcraft_run(
        _ready_request(tmp_path, seed=8),
        "bindcraft-conflict-key",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert first.execution_status == "complete"
    assert first.status == "failed"
    assert second.status == "conflict"
    assert second.error == "idempotency_key_conflict"
    assert second.created is False
    assert second.idempotent_replay is False
    assert second.run_id is None
    assert generator.calls == 1
    assert supervisor_factory.calls == 1
    assert len(_idempotency_records(tmp_path)) == 1


def test_same_science_ignores_run_and_execution_identity(tmp_path):
    supervisor_factory = CountingSupervisorFactory()
    generator = CountingGenerator()
    starter = _starter(tmp_path, supervisor_factory)

    first_request = _ready_request(
        tmp_path,
        run_id="protein_run_first",
        execution_id="exec_first",
    )
    second_request = replace(
        first_request,
        run_id="protein_run_second",
        execution_id="exec_second",
        output_root=tmp_path / "other-output",
    )

    first = starter.start_bindcraft_run(
        first_request,
        "bindcraft-stable-id-key",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    second = starter.start_bindcraft_run(
        second_request,
        "bindcraft-stable-id-key",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert first.idempotent_replay is False
    assert first.execution_status == "complete"
    assert first.status == "failed"
    assert second.idempotent_replay is True
    assert second.run_id == first.run_id
    assert second.run_id == "protein_run_first"
    assert generator.calls == 1
    assert supervisor_factory.calls == 1


def test_changed_advanced_profile_changes_scientific_identity(tmp_path):
    supervisor_factory = CountingSupervisorFactory()
    first_profile = _safe_profile(tmp_path / "profile_a.json")
    second_profile = _safe_profile(
        tmp_path / "profile_b.json",
        design_algorithm="4stage",
        extra="different",
    )
    starter = _starter(tmp_path, supervisor_factory)
    request = _ready_request(tmp_path)

    first = starter.start_bindcraft_run(
        request,
        "bindcraft-profile-key",
        generator=CountingGenerator(first_profile),
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    second = starter.start_bindcraft_run(
        request,
        "bindcraft-profile-key",
        generator=CountingGenerator(second_profile),
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert first.execution_status == "complete"
    assert first.status == "failed"
    assert second.status == "conflict"
    assert advanced_profile_identity_sha256(first_profile) != (
        advanced_profile_identity_sha256(second_profile)
    )
    assert supervisor_factory.calls == 1


def _science_hash(request, generator) -> str:
    return bindcraft_run_request_hash(
        request,
        advanced_profile_sha256=(
            generator.effective_advanced_profile_sha256()
        ),
    )


def test_changed_scientific_inputs_change_claimed_identity(tmp_path):
    supervisor_factory = CountingSupervisorFactory()
    generator = CountingGenerator()
    starter = _starter(tmp_path, supervisor_factory)
    baseline = _ready_request(tmp_path)
    baseline_hash = _science_hash(baseline, generator)

    starter.start_bindcraft_run(
        baseline,
        "bindcraft-science-key",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )

    assert baseline.target_path is not None
    original_target = baseline.target_path.read_bytes()
    baseline.target_path.write_text(
        "ATOM\nATOM\n",
        encoding="utf-8",
    )
    assert _science_hash(baseline, generator) != baseline_hash

    target_conflict = starter.start_bindcraft_run(
        baseline,
        "bindcraft-science-key",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    assert target_conflict.status == "conflict"

    baseline.target_path.write_bytes(original_target)
    assert baseline.settings_path is not None
    original_settings = baseline.settings_path.read_bytes()
    baseline.settings_path.write_text(
        '{"chains":"B"}\n',
        encoding="utf-8",
    )
    assert _science_hash(baseline, generator) != baseline_hash
    baseline.settings_path.write_bytes(original_settings)

    checkpoint = tmp_path / "checkpoint.bin"
    checkpoint.write_bytes(b"checkpoint-v1")
    assert _science_hash(
        replace(baseline, checkpoint_path=checkpoint),
        generator,
    ) != baseline_hash
    assert _science_hash(
        replace(baseline, container_digest="c" * 64),
        generator,
    ) != baseline_hash
    assert _science_hash(
        replace(baseline, tool_version="bindcraft-other-version"),
        generator,
    ) != baseline_hash
    assert _science_hash(
        replace(
            baseline,
            generation=replace(
                baseline.generation,
                constraints={
                    **baseline.generation.constraints,
                    "length_max": 70,
                },
            ),
        ),
        generator,
    ) != baseline_hash

    contaminated = replace(
        baseline,
        generation=replace(
            baseline.generation,
            constraints={
                **baseline.generation.constraints,
                "ipsae": 0.99,
                "ranking_score": 1.0,
                "rank": 1,
                "selected": True,
            },
        ),
    )
    assert _science_hash(contaminated, generator) == baseline_hash

    replay = starter.start_bindcraft_run(
        contaminated,
        "bindcraft-science-key",
        generator=generator,
        authorization=developer_bindcraft_authorization(),
        gpu_available=True,
    )
    assert replay.idempotent_replay is True
    assert generator.calls == 1
    assert supervisor_factory.calls == 1


def test_concurrent_same_key_has_one_first_claimant(tmp_path):
    supervisor_factory = CountingSupervisorFactory()
    runtime = HoldingRuntime()
    generator = CountingGenerator(runtime=runtime)
    starter = _starter(tmp_path, supervisor_factory)
    request = _ready_request(tmp_path)
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        return starter.start_bindcraft_run(
            request,
            "bindcraft-concurrent-key",
            generator=generator,
            authorization=developer_bindcraft_authorization(),
            gpu_available=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(go), pool.submit(go)]
        assert runtime.entered.wait(timeout=5)
        runtime.hold.set()
        results = [future.result(timeout=10) for future in futures]

    assert {item.idempotent_replay for item in results} == {
        False,
        True,
    }
    assert {item.execution_status for item in results} == {
        "complete",
    }
    assert {item.status for item in results} == {"failed"}
    assert generator.calls == 1
    assert supervisor_factory.calls == 1
    assert len(_idempotency_records(tmp_path)) == 1
