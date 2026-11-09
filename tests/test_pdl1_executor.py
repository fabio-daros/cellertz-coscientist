"""Phase 6M.1 PDL1 registration contract. Runtime doubles only."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import fields, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.pdl1_executor import (
    ARGV_POLICY_VERSION,
    DIRECTORY_POLICY,
    ENVIRONMENT_POLICY,
    UNBOUND_INPUT_FIELDS,
    Pdl1ExecutionPlan,
    Pdl1ExecutionSpecification,
    Pdl1InputBinding,
    Pdl1RuntimeResult,
    Pdl1ScientificRunExecutor,
    RuntimeCapabilities,
    RuntimeOutputEvidence,
    register_pdl1_executor,
)
from cellertz.application.scientific_run import (
    DISABLED_TEXT,
    FAILED_TEXT,
    INTERRUPTED_TEXT,
    InMemoryScientificRunStore,
    ScientificRunService,
)
from cellertz.application.scientific_run_config import resolve_scientific_run_execution
from cellertz.application.scientific_run_store import SQLiteScientificRunStore
from cellertz.application.scientific_runner_readiness import (
    CD22_PROFILE_ID,
    TACI_PROFILE_ID,
    load_scientific_runner_readiness,
)
from tests.test_scientific_authorization import SESSION, WHEN, _authorize, _ready_readiness, _world

_CAPABLE = RuntimeCapabilities(
    enforces_timeout=True,
    cleanup="process_group",
    output_limit="inflight",
    resources=frozenset({"cpu_count", "memory_mib", "gpu_count"}),
    explicit_environment=True,
)


class Double:
    def __init__(self, result: Pdl1RuntimeResult | None = None, capabilities=None, error=None):
        self.calls = 0
        self.plans: list[Pdl1ExecutionPlan] = []
        self.result = result
        self._capabilities = _CAPABLE if capabilities is None else capabilities
        self.error = error

    def capabilities(self) -> RuntimeCapabilities:
        return self._capabilities

    def execute(self, plan: Pdl1ExecutionPlan) -> Pdl1RuntimeResult:
        self.calls += 1
        self.plans.append(plan)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _document(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _specification(path: Path, observed_at: str, **overrides: object) -> Pdl1ExecutionSpecification:
    document = _document(path)
    envelope = document["resource_envelope"]
    values: dict[str, object] = {
        "profile_id": document["profile_id"],
        "target_scope": document["target_scope"],
        "tool_name": document["tool_name"],
        "tool_version": document["tool_version"],
        "runtime_name": document["runtime_name"],
        "runtime_version": document["runtime_version"],
        "image_digest": document["image_digest"],
        "manifest_digest": _digest(path),
        "entrypoint": document["entrypoint"],
        "recorded_argv_template": tuple(document["argv_template"]),
        "timeout_seconds": document["timeout_seconds"],
        "max_output_bytes": document["max_output_bytes"],
        "cpu_count": envelope["cpu_count"],
        "memory_mib": envelope["memory_mib"],
        "gpu_count": envelope["gpu_count"],
        "target_structure_sha256": "11" * 32,
        "settings_sha256": "22" * 32,
        "advanced_profile_sha256": "33" * 32,
        "readiness_observed_at": observed_at,
        "argv_policy_version": ARGV_POLICY_VERSION,
        "directory_policy": DIRECTORY_POLICY,
    }
    values.update(overrides)
    return Pdl1ExecutionSpecification(**values)  # type: ignore[arg-type]


def _binding(spec: Pdl1ExecutionSpecification, request_digest: str) -> Pdl1InputBinding:
    return Pdl1InputBinding(
        request_digest,
        spec.target_structure_sha256,
        spec.settings_sha256,
        spec.advanced_profile_sha256,
    )


def _success(size: int = 12) -> Pdl1RuntimeResult:
    return Pdl1RuntimeResult(
        status="completed",
        exit_code=0,
        cleanup="stopped",
        output_bytes=size,
        outputs=(RuntimeOutputEvidence("design_records", "text/csv", size),),
        inflight_output_limit_enforced=True,
        resources_enforced=True,
    )


def _opened(tmp_path: Path):
    authorizations, _runs, _runner, _gate, _drafter, approval, _finished, _probe = _world(tmp_path)
    granted = _authorize(authorizations, approval.approval_id)
    path = tmp_path / "manifest.json"
    snapshot = authorizations._readiness.read()
    spec = _specification(path, snapshot.observed_at)
    return authorizations, granted, path, snapshot, spec


def test_missing_malformed_and_incomplete_registration(tmp_path: Path) -> None:
    _authorizations, _granted, path, snapshot, spec = _opened(tmp_path)
    runtime = Double()
    missing = register_pdl1_executor(None, str(path), snapshot, runtime)
    assert missing.code == "pdl1_execution_specification_invalid"
    assert missing.accepted is False
    assert missing.executor is None
    assert runtime.calls == 0
    with pytest.raises(ApplicationContractError) as bare:
        _specification(path, snapshot.observed_at, image_digest="ab" * 32)
    assert bare.value.code == "pdl1_execution_specification_invalid"
    with pytest.raises(ApplicationContractError) as incomplete:
        _specification(path, snapshot.observed_at, advanced_profile_sha256="")
    assert incomplete.value.code == "pdl1_execution_specification_invalid"
    absent = register_pdl1_executor(spec, str(tmp_path / "missing.json"), snapshot, runtime)
    assert absent.code == "manifest_unreadable"
    assert str(tmp_path) not in absent.code
    blocked = load_scientific_runner_readiness(
        session_id=SESSION, environ={}, clock=lambda: WHEN
    ).read()
    disabled = register_pdl1_executor(spec, str(path), blocked, runtime)
    assert disabled.code == "readiness_status" or disabled.field == "readiness_status"
    assert disabled.executor is None


def test_blocked_targets_cannot_become_a_specification(tmp_path: Path) -> None:
    _authorizations, _granted, path, snapshot, _spec = _opened(tmp_path)
    for profile, target in ((CD22_PROFILE_ID, "CD22"), (TACI_PROFILE_ID, "TACI")):
        with pytest.raises(ApplicationContractError) as error:
            _specification(path, snapshot.observed_at, profile_id=profile, target_scope=target)
        assert error.value.code == "pdl1_execution_specification_invalid"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tool_version", "other-tool-1"),
        ("runtime_name", "other-runtime"),
        ("runtime_version", "other-runtime-2"),
        ("image_digest", "sha256:" + ("cd" * 32)),
        ("entrypoint", "other-entry"),
        ("timeout_seconds", 90),
        ("max_output_bytes", 2048),
        ("cpu_count", 4),
        ("memory_mib", 2048),
        ("gpu_count", 1),
        ("recorded_argv_template", ("--other-template",)),
        ("manifest_digest", "ff" * 32),
    ],
)
def test_policy_identity_mismatch_rejects_registration(tmp_path: Path, field: str, value) -> None:
    _authorizations, _granted, path, snapshot, spec = _opened(tmp_path)
    runtime = Double()
    shifted = replace(spec, **{field: value})
    registration = register_pdl1_executor(shifted, str(path), snapshot, runtime)
    assert registration.code == "pdl1_execution_policy_mismatch"
    assert registration.field == field
    assert registration.executor is None
    assert runtime.calls == 0


def test_snapshot_mismatch_and_digest_alone_do_not_register(tmp_path: Path) -> None:
    _authorizations, _granted, path, snapshot, spec = _opened(tmp_path)
    runtime = Double(_success())
    shifted = replace(snapshot, tool_version="other-tool-1")
    mismatch = register_pdl1_executor(spec, str(path), shifted, runtime)
    assert mismatch.code == "pdl1_execution_policy_mismatch"
    assert mismatch.field == "tool_version"
    other = replace(spec, target_structure_sha256="aa" * 32)
    first = register_pdl1_executor(spec, str(path), snapshot, runtime)
    second = register_pdl1_executor(other, str(path), snapshot, runtime)
    assert first.code == second.code == "pdl1_input_identity_unbound"
    assert first.missing == second.missing == UNBOUND_INPUT_FIELDS
    assert first.executor is None and second.executor is None
    bound = register_pdl1_executor(
        spec,
        str(path),
        snapshot,
        runtime,
        input_binding=_binding(spec, "ab" * 32),
    )
    assert bound.code == "pdl1_input_identity_unbound"
    assert bound.accepted is False
    assert runtime.calls == 0


def test_input_binding_mismatch_is_not_registered(tmp_path: Path) -> None:
    _authorizations, _granted, path, snapshot, spec = _opened(tmp_path)
    runtime = Double()
    binding = Pdl1InputBinding(
        "ab" * 32, "aa" * 32, spec.settings_sha256, spec.advanced_profile_sha256
    )
    registration = register_pdl1_executor(spec, str(path), snapshot, runtime, input_binding=binding)
    assert registration.code == "pdl1_input_binding_mismatch"
    assert runtime.calls == 0


def test_unsupported_enforcement_rejects_before_invocation(tmp_path: Path) -> None:
    authorizations, granted, path, snapshot, spec = _opened(tmp_path)
    post = Double(
        capabilities=replace(
            _CAPABLE,
            output_limit="post_directory_size",
        )
    )
    resources = Double(
        capabilities=replace(_CAPABLE, resources=frozenset({"cpu_count", "memory_mib"}))
    )
    assert register_pdl1_executor(spec, str(path), snapshot, post).code == (
        "pdl1_output_limit_unenforced"
    )
    assert (
        register_pdl1_executor(spec, str(path), snapshot, resources).code
        == "pdl1_resource_enforcement_unavailable"
    )
    store = InMemoryScientificRunStore()
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=Pdl1ScientificRunExecutor(
            spec, post, _binding(spec, granted.authorization.request_digest)
        ),
        execution_enabled=True,
        clock=lambda: WHEN,
        run_ids=lambda: "run_" + "ab" * 16,
    )
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    failed = service.process_next()
    assert failed.status == "failed"
    assert failed.text == FAILED_TEXT
    assert post.calls == 0
    assert resources.calls == 0
    store.close()


def test_authorization_rejection_and_ineligible_runs_do_not_invoke_runtime(tmp_path: Path) -> None:
    authorizations, granted, _path, _snapshot, spec = _opened(tmp_path)
    runtime = Double(_success())
    executor = Pdl1ScientificRunExecutor(
        spec, runtime, _binding(spec, granted.authorization.request_digest)
    )
    store = InMemoryScientificRunStore()
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=True,
        clock=lambda: WHEN,
        run_ids=lambda: "run_" + "ab" * 16,
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    assert executor.execute(created.run).status == "failed"
    assert executor.execute(replace(created.run, confirmed=False)).status == "failed"
    shifted, _probe = _ready_readiness(
        tmp_path / "shift", when=datetime(2026, 10, 12, 18, 0, tzinfo=UTC)
    )
    authorizations._readiness = shifted
    failed = service.process_next()
    assert failed.status == "failed"
    assert failed.text == FAILED_TEXT
    assert runtime.calls == 0
    store.close()


def test_disabled_execution_does_not_call_the_pdl1_executor(tmp_path: Path) -> None:
    authorizations, granted, _path, _snapshot, spec = _opened(tmp_path)
    runtime = Double(_success())
    executor = Pdl1ScientificRunExecutor(
        spec, runtime, _binding(spec, granted.authorization.request_digest)
    )
    store = InMemoryScientificRunStore()
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=False,
        clock=lambda: WHEN,
        run_ids=lambda: "run_" + "cd" * 16,
    )
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    failed = service.process_next()
    assert failed.text == DISABLED_TEXT
    assert runtime.calls == 0
    store.close()


def test_plan_uses_explicit_environment_and_ignores_argv_template(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("APPTAINERENV_CUDA_VISIBLE_DEVICES", "0")
    authorizations, granted, _path, _snapshot, spec = _opened(tmp_path)
    runtime = Double(_success())
    store = InMemoryScientificRunStore()
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=Pdl1ScientificRunExecutor(
            spec, runtime, _binding(spec, granted.authorization.request_digest)
        ),
        execution_enabled=True,
        clock=lambda: WHEN,
        run_ids=lambda: "run_" + "ab" * 16,
    )
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    finished = service.process_next()
    assert finished.status == "completed"
    plan = runtime.plans[0]
    names = {item.name for item in fields(plan)}
    assert "argv" not in names
    assert "--recorded-template" not in repr(plan)
    assert plan.environment == ()
    assert plan.environment_policy == ENVIRONMENT_POLICY
    assert plan.directory_policy == DIRECTORY_POLICY
    assert plan.argv_policy_version == ARGV_POLICY_VERSION
    assert os.environ["APPTAINERENV_CUDA_VISIBLE_DEVICES"] == "0"
    assert all("APPTAINERENV_CUDA_VISIBLE_DEVICES" not in pair for pair in plan.environment)
    kinds = [item.kind for item in finished.artifacts]
    assert kinds == ["execution_record"]
    assert finished.scientific_validation is False
    assert finished.scientific_candidate_generated is False
    assert finished.ranked is False
    assert finished.accepted is False
    store.close()


def test_runtime_failures_do_not_become_success(tmp_path: Path) -> None:
    cases = [
        Pdl1RuntimeResult("timed_out", None, "uncertain", 0, (), False, False),
        Pdl1RuntimeResult("completed", 0, "stopped", 0, (), True, True),
        Pdl1RuntimeResult(
            "completed",
            0,
            "stopped",
            4,
            (RuntimeOutputEvidence("design_records", "chemical/x-pdb", 4),),
            True,
            True,
        ),
        Pdl1RuntimeResult(
            "completed",
            0,
            "stopped",
            1_048_577,
            (RuntimeOutputEvidence("design_records", "text/csv", 1_048_577),),
            True,
            True,
        ),
        Pdl1RuntimeResult(
            "completed",
            0,
            "stopped",
            4,
            (RuntimeOutputEvidence("design_records", "text/csv", 4),),
            False,
            True,
        ),
    ]
    for index, result in enumerate(cases):
        authorizations, granted, _path, _snapshot, spec = _opened(tmp_path / f"case-{index}")
        if result.output_bytes == 1_048_577:
            result = replace(result, output_bytes=spec.max_output_bytes + 1)
            result = replace(
                result,
                outputs=(
                    RuntimeOutputEvidence("design_records", "text/csv", spec.max_output_bytes + 1),
                ),
            )
        runtime = Double(result)
        store = InMemoryScientificRunStore()
        service = ScientificRunService(
            authorizations,
            store=store,
            executor=Pdl1ScientificRunExecutor(
                spec, runtime, _binding(spec, granted.authorization.request_digest)
            ),
            execution_enabled=True,
            clock=lambda: WHEN,
            run_ids=lambda: "run_" + "ef" * 16,
        )
        service.create(
            session_id=SESSION,
            authorization_id=granted.authorization.authorization_id,
            idempotency_key="run-1",
        )
        failed = service.process_next()
        assert failed is not None
        assert failed.status == "failed"
        assert failed.artifacts == ()
        assert "traceback" not in failed.text
        assert runtime.calls == 1
        store.close()


def test_runtime_exception_does_not_leak(tmp_path: Path) -> None:
    authorizations, granted, _path, _snapshot, spec = _opened(tmp_path)
    runtime = Double(error=RuntimeError("subprocess /tmp/secret --argv"))
    store = InMemoryScientificRunStore()
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=Pdl1ScientificRunExecutor(
            spec, runtime, _binding(spec, granted.authorization.request_digest)
        ),
        execution_enabled=True,
        clock=lambda: WHEN,
        run_ids=lambda: "run_" + "ab" * 16,
    )
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    failed = service.process_next()
    assert failed.status == "failed"
    assert failed.text == FAILED_TEXT
    assert "/tmp" not in failed.text
    assert "argv" not in failed.text
    store.close()


def test_incompatible_request_and_identity_do_not_invoke_runtime(tmp_path: Path) -> None:
    authorizations, granted, _path, _snapshot, spec = _opened(tmp_path)
    runtime = Double(_success())
    store = InMemoryScientificRunStore()
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=Pdl1ScientificRunExecutor(spec, runtime, _binding(spec, "ff" * 32)),
        execution_enabled=True,
        clock=lambda: WHEN,
        run_ids=lambda: "run_" + "ab" * 16,
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    running = store.claim_next_queued(recorded_at=WHEN.isoformat())
    assert running.run_id == created.run.run_id
    assert service._executor.execute(running).status == "failed"
    unlabeled = Pdl1ScientificRunExecutor(spec, runtime, None)
    assert unlabeled.execute(running).status == "failed"
    mismatched = Pdl1ScientificRunExecutor(
        replace(spec, image_digest="sha256:" + ("cd" * 32)),
        runtime,
        _binding(spec, granted.authorization.request_digest),
    )
    assert mismatched.execute(running).status == "failed"
    assert runtime.calls == 0
    store.close()


def test_replay_retry_and_recovery(tmp_path: Path) -> None:
    authorizations, granted, _path, _snapshot, spec = _opened(tmp_path)
    runtime = Double(_success())
    binding = _binding(spec, granted.authorization.request_digest)
    store = InMemoryScientificRunStore()
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=Pdl1ScientificRunExecutor(spec, runtime, binding),
        execution_enabled=True,
        clock=lambda: WHEN,
        run_ids=lambda: "run_" + "ab" * 16,
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    finished = service.process_next()
    assert finished.status == "completed"
    assert runtime.calls == 1
    assert runtime.plans[0].attempt == 1
    replay = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    assert replay.run.run_id == created.run.run_id
    assert service.process_next() is None
    assert runtime.calls == 1
    store.close()

    retry_auth, retry_granted, _path, _snapshot, retry_spec = _opened(tmp_path / "retry")
    script = Double()
    script.result = Pdl1RuntimeResult("timed_out", None, "uncertain", 0, (), False, False)
    retry_binding = _binding(retry_spec, retry_granted.authorization.request_digest)
    retry_store = InMemoryScientificRunStore()
    retry_service = ScientificRunService(
        retry_auth,
        store=retry_store,
        executor=Pdl1ScientificRunExecutor(retry_spec, script, retry_binding),
        execution_enabled=True,
        clock=lambda: WHEN,
        run_ids=lambda: "run_" + "cd" * 16,
    )
    queued = retry_service.create(
        session_id=SESSION,
        authorization_id=retry_granted.authorization.authorization_id,
        idempotency_key="run-retry",
    )
    assert retry_service.process_next().status == "failed"
    script.result = _success()
    retried = retry_service.retry(
        session_id=SESSION, run_id=queued.run.run_id, idempotency_key="retry-1"
    )
    assert retried.run.attempt == 2
    completed = retry_service.process_next()
    assert completed.status == "completed"
    assert completed.attempt == 2
    assert script.plans[0].attempt == 1
    assert script.plans[1].attempt == 2
    assert script.plans[1].run_id == script.plans[0].run_id
    retry_store.close()

    recovery_auth, recovery_granted, _path, _snapshot, recovery_spec = _opened(tmp_path / "recover")
    recovery_binding = _binding(recovery_spec, recovery_granted.authorization.request_digest)
    recovery_runtime = Double(_success())
    durable = tmp_path / "recover.sqlite3"
    durable_store = SQLiteScientificRunStore(durable)
    recovery = ScientificRunService(
        recovery_auth,
        store=durable_store,
        executor=Pdl1ScientificRunExecutor(recovery_spec, recovery_runtime, recovery_binding),
        execution_enabled=True,
        clock=lambda: WHEN,
        run_ids=lambda: "run_" + "ef" * 16,
    )
    recovery.create(
        session_id=SESSION,
        authorization_id=recovery_granted.authorization.authorization_id,
        idempotency_key="run-recover",
    )
    durable_store.close()
    claimed = SQLiteScientificRunStore(durable)
    running = claimed.claim_next_queued(recorded_at=WHEN.isoformat())
    assert running.status == "running"
    claimed.close()
    restarted_runtime = Double(_success())
    restarted_store = SQLiteScientificRunStore(durable)
    restarted = ScientificRunService(
        recovery_auth,
        store=restarted_store,
        executor=Pdl1ScientificRunExecutor(recovery_spec, restarted_runtime, recovery_binding),
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    interrupted = restarted.startup()
    assert interrupted[0].text == INTERRUPTED_TEXT
    assert restarted.process_next() is None
    assert recovery_runtime.calls == 0
    assert restarted_runtime.calls == 0
    restarted_store.close()


def test_production_composition_stays_closed() -> None:
    source = Path("src/cellertz/conversation/__main__.py").read_text(encoding="utf-8")
    assert "prepare_enabled_infrastructure(" in source
    assert "register_pdl1_executor(" not in source
    assert "CliDockerControl" not in source
    assert resolve_scientific_run_execution({}) == "disabled"
    assert (
        resolve_scientific_run_execution({"CELLERTZ_SCIENTIFIC_RUN_EXECUTION": "governed-fixture"})
        == "governed-fixture"
    )


def test_disabled_snapshot_field_name(tmp_path: Path) -> None:
    _authorizations, _granted, path, _snapshot, spec = _opened(tmp_path)
    blocked = load_scientific_runner_readiness(
        session_id=SESSION, environ={}, clock=lambda: WHEN
    ).read()
    registration = register_pdl1_executor(spec, str(path), blocked, Double())
    assert registration.field == "readiness_status"


def _claimed(tmp_path: Path, result: Pdl1RuntimeResult):
    authorizations, granted, _path, _snapshot, spec = _opened(tmp_path)
    runtime = Double(result)
    store = InMemoryScientificRunStore()
    service = ScientificRunService(
        authorizations,
        store=store,
        executor=Pdl1ScientificRunExecutor(
            spec, runtime, _binding(spec, granted.authorization.request_digest)
        ),
        execution_enabled=True,
        clock=lambda: WHEN,
        run_ids=lambda: "run_" + "ab" * 16,
    )
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    running = store.claim_next_queued(recorded_at=WHEN.isoformat())
    return store, runtime, service._executor.execute(running)


@pytest.mark.parametrize("exit_code", [False, True, 0.0, "0", None])
def test_non_integer_exit_codes_are_rejected(tmp_path: Path, exit_code: object) -> None:
    store, runtime, finished = _claimed(tmp_path, replace(_success(), exit_code=exit_code))
    assert finished.status == "failed"
    assert finished.artifacts == ()
    assert runtime.calls == 1
    store.close()


def test_integer_zero_exit_code_remains_accepted(tmp_path: Path) -> None:
    store, runtime, finished = _claimed(tmp_path, _success())
    assert finished.status == "completed"
    assert finished.artifacts[0].kind == "execution_record"
    assert runtime.calls == 1
    store.close()


_BAD_STRINGS = [None, 1, True, b"value", ["value"], {"value": "value"}]


@pytest.mark.parametrize(
    "field",
    [item.name for item in fields(Pdl1ExecutionSpecification) if item.type == "str"],
)
@pytest.mark.parametrize("value", _BAD_STRINGS)
def test_malformed_specification_strings_use_the_contract_error(
    tmp_path: Path, field: str, value: object
) -> None:
    service, _probe = _ready_readiness(tmp_path)
    with pytest.raises(ApplicationContractError) as error:
        _specification(tmp_path / "manifest.json", service.read().observed_at, **{field: value})
    assert error.value.code == "pdl1_execution_specification_invalid"
    assert str(error.value) == "pdl1_execution_specification_invalid"


@pytest.mark.parametrize(
    "argv",
    [
        None,
        1,
        True,
        b"--recorded-template",
        ["--recorded-template"],
        {"argv": "--recorded-template"},
        (None,),
        (1,),
        (True,),
        (b"--recorded-template",),
        (["--recorded-template"],),
        ({"argv": "--recorded-template"},),
    ],
)
def test_malformed_argv_templates_use_the_contract_error(tmp_path: Path, argv: object) -> None:
    service, _probe = _ready_readiness(tmp_path)
    with pytest.raises(ApplicationContractError) as error:
        _specification(
            tmp_path / "manifest.json",
            service.read().observed_at,
            recorded_argv_template=argv,
        )
    assert error.value.code == "pdl1_execution_specification_invalid"


@pytest.mark.parametrize(
    "kind",
    ["relative", "nested", "nul_suffix", "bare_nul", "relative_nul"],
)
def test_non_absolute_and_nul_manifest_paths_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    _authorizations, _granted, path, snapshot, spec = _opened(tmp_path)
    monkeypatch.chdir(path.parent)
    manifest_path = {
        "relative": path.name,
        "nested": "nested/manifest.json",
        "nul_suffix": str(path) + "\0",
        "bare_nul": "\0",
        "relative_nul": path.name + "\0",
    }[kind]
    registration = register_pdl1_executor(spec, manifest_path, snapshot, Double())
    assert registration.code == "pdl1_execution_specification_invalid"
    assert registration.executor is None
    rendered = repr(registration)
    assert manifest_path not in rendered
    assert "ValueError" not in rendered
    assert "OSError" not in rendered
    assert "embedded null" not in rendered
