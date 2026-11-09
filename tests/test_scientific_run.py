"""Phase 6K scientific-run execution. One authorization, one run."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cellertz.application.conversation_transport import configured_handler
from cellertz.application.scientific_authorization import CONSUMED_TEXT
from cellertz.application.scientific_run import (
    _KEY_TEXT,
    _RETRY_TEXT,
    COMPLETED_TEXT,
    DISABLED_TEXT,
    FAILED_TEXT,
    INTERRUPTED_TEXT,
    QUEUED_TEXT,
    RETRY_QUEUED_TEXT,
    RUNNING_TEXT,
    ExecutorArtifact,
    InMemoryScientificRunStore,
    ScientificExecutionResult,
    ScientificRunReply,
    ScientificRunService,
    ScientificRunStoreError,
    _canonical,
    run_document,
    run_from_document,
)
from cellertz.application.scientific_run_config import (
    ScientificRunConfigurationError,
    open_scientific_run_store,
    resolve_scientific_run_backend,
    resolve_scientific_run_execution,
    resolve_scientific_run_worker_poll_seconds,
)
from cellertz.application.scientific_run_store import SQLiteScientificRunStore
from cellertz.application.scientific_run_transport import RUNS_PATH, handle_scientific_run
from cellertz.application.scientific_run_worker import ScientificRunWorker
from cellertz.conversation.__main__ import main
from tests.test_scientific_authorization import SESSION, WHEN, _authorize, _ready_readiness, _world

ROOT = Path(__file__).resolve().parents[1]
_MODULES = (
    ROOT / "src/cellertz/application/scientific_run.py",
    ROOT / "src/cellertz/application/scientific_run_store.py",
    ROOT / "src/cellertz/application/scientific_run_config.py",
    ROOT / "src/cellertz/application/scientific_run_transport.py",
    ROOT / "src/cellertz/application/scientific_run_worker.py",
    ROOT / "src/cellertz/application/governed_agent_runtime.py",
)


class RecordingExecutor:
    def __init__(
        self, result: ScientificExecutionResult | None = None, sink: list[str] | None = None
    ):
        self.calls = 0
        self.result = result
        self.sink = sink if sink is not None else []

    def execute(self, run):
        self.calls += 1
        self.sink.append(run.status)
        if self.result is not None:
            return self.result
        return ScientificExecutionResult(
            "completed",
            (ExecutorArtifact("execution_record", "application/json", _record(run)),),
        )


def _record(run) -> bytes:
    return _canonical(
        {
            "attempt": run.attempt,
            "authorization_id": run.authorization_id,
            "run_id": run.run_id,
            "schema": "cellertz.scientific_execution_record.v1",
            "scientific_validation": False,
            "session_id": run.session_id,
            "status": "completed",
        }
    ).encode()


def _candidate(run) -> bytes:
    return _canonical(
        {
            "candidate_id": "candidate_" + "ab" * 16,
            "schema": "cellertz.scientific_candidate.v1",
            "sequence": "ACDEFGHIK",
            "target_scope": run.target_scope,
        }
    ).encode()


def _service(tmp_path: Path, backend: str, *, enabled: bool = False, executor=None, when=WHEN):
    if backend == "memory":
        store = InMemoryScientificRunStore()
    else:
        store = SQLiteScientificRunStore(tmp_path / "scientific-runs.sqlite3")
    authorizations, _runs, runner, _gate, _drafter, approval, _finished, _probe = _world(
        tmp_path, when=when
    )
    granted = _authorize(authorizations, approval.approval_id)
    ids = {"n": 0}

    def run_ids() -> str:
        ids["n"] += 1
        return f"run_{ids['n']:032x}"

    service = ScientificRunService(
        authorizations,
        store=store,
        executor=executor,
        execution_enabled=enabled,
        clock=lambda: when,
        run_ids=run_ids,
    )
    return service, store, authorizations, granted, runner


@pytest.fixture(params=["memory", "sqlite"])
def backend(request):
    return request.param


def test_create_consumes_one_authorization_and_queues(tmp_path: Path, backend: str) -> None:
    service, store, authorizations, granted, runner = _service(tmp_path, backend)
    calls = runner.calls
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    assert created.status == "run_queued"
    assert created.run is not None
    assert created.run.status == "queued"
    assert created.run.attempt == 1
    assert created.run.scientific_execution is False
    assert created.run.scientific_validation is False
    assert created.run.text == QUEUED_TEXT
    observed = authorizations.read(
        session_id=SESSION, authorization_id=granted.authorization.authorization_id
    )
    assert observed.authorization.consumed_at is not None
    assert observed.text == CONSUMED_TEXT
    replay = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    other_key = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-2",
    )
    assert replay.run.run_id == created.run.run_id
    assert other_key.run.run_id == created.run.run_id
    assert len(service.list_runs(session_id=SESSION).runs) == 1
    assert (
        service.read(session_id="session_other", run_id=created.run.run_id).status == "run_absent"
    )
    assert runner.calls == calls
    store.close()


def test_failed_create_leaves_the_authorization_unconsumed(tmp_path: Path, backend: str) -> None:
    service, store, authorizations, granted, _runner = _service(tmp_path, backend)
    store._fail_on = "create_run"
    failed = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    assert failed.status == "run_record_invalid"
    assert failed.run is None
    observed = authorizations.read(
        session_id=SESSION, authorization_id=granted.authorization.authorization_id
    )
    assert observed.authorization.consumed_at is None
    assert observed.authorization.execution_authorized is True
    store._fail_on = None
    assert service.list_runs(session_id=SESSION).runs == ()
    store.close()


def test_consumed_authorization_without_confirm_is_repaired(tmp_path: Path, backend: str) -> None:
    service, store, authorizations, granted, _runner = _service(tmp_path, backend)
    store._fail_on = "confirm"
    failed = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    assert failed.status == "run_record_invalid"
    observed = authorizations.read(
        session_id=SESSION, authorization_id=granted.authorization.authorization_id
    )
    assert observed.authorization.consumed_at is not None
    assert service.read(session_id=SESSION, run_id="run_" + "0" * 31 + "1").status == "run_absent"
    store._fail_on = None
    service.startup()
    listed = service.list_runs(session_id=SESSION)
    assert len(listed.runs) == 1
    assert listed.runs[0].authorization_id == granted.authorization.authorization_id
    store.close()


def test_crash_before_consume_is_finished_on_startup(tmp_path: Path, backend: str) -> None:
    service, store, authorizations, granted, _runner = _service(tmp_path, backend)
    original = authorizations.claim_once

    def boom(**_kwargs):
        raise RuntimeError("stopped before consume")

    authorizations.claim_once = boom
    with pytest.raises(RuntimeError):
        service.create(
            session_id=SESSION,
            authorization_id=granted.authorization.authorization_id,
            idempotency_key="run-1",
        )
    authorizations.claim_once = original
    assert (
        authorizations.read(
            session_id=SESSION, authorization_id=granted.authorization.authorization_id
        ).authorization.consumed_at
        is None
    )
    service.startup()
    listed = service.list_runs(session_id=SESSION)
    assert len(listed.runs) == 1
    assert (
        authorizations.read(
            session_id=SESSION, authorization_id=granted.authorization.authorization_id
        ).authorization.consumed_at
        is not None
    )
    store.close()


def test_concurrent_creates_produce_one_run(tmp_path: Path, backend: str) -> None:
    service, store, _authorizations, granted, _runner = _service(tmp_path, backend)
    barrier = threading.Barrier(8)
    found: list[str] = []

    def attempt(key: str) -> None:
        barrier.wait()
        reply = service.create(
            session_id=SESSION,
            authorization_id=granted.authorization.authorization_id,
            idempotency_key=key,
        )
        assert reply.run is not None
        found.append(reply.run.run_id)

    threads = [threading.Thread(target=attempt, args=(f"key-{index}",)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(found)) == 1
    assert len(service.list_runs(session_id=SESSION).runs) == 1
    store.close()


def test_disabled_execution_does_not_call_the_executor(tmp_path: Path, backend: str) -> None:
    executor = RecordingExecutor()
    service, store, _authorizations, granted, runner = _service(
        tmp_path, backend, enabled=False, executor=executor
    )
    calls = runner.calls
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    finished = service.process_next()
    assert executor.calls == 0
    assert finished is not None
    assert finished.status == "failed"
    assert finished.text == DISABLED_TEXT
    assert finished.scientific_execution is False
    assert finished.artifacts == ()
    assert service.process_next() is None
    again = service.process_next()
    assert again is None
    blocked = service.retry(
        session_id=SESSION, run_id=created.run.run_id, idempotency_key="retry-1"
    )
    assert blocked.status == "run_queued"
    assert blocked.run.attempt == 2
    assert blocked.run.run_id == created.run.run_id
    retried = service.process_next()
    assert retried.status == "failed"
    assert retried.attempt == 2
    assert executor.calls == 0
    assert runner.calls == calls
    store.close()


def test_executor_runs_outside_the_transaction_and_fail_closed(tmp_path: Path) -> None:
    seen: list[str] = []
    store_holder: dict[str, object] = {}

    class Watching(RecordingExecutor):
        def execute(self, run):
            self.calls += 1
            seen.append(run.status)
            connection = store_holder["store"]._conn
            assert connection.in_transaction is False
            if self.calls == 1:
                return ScientificExecutionResult(
                    "completed",
                    (
                        ExecutorArtifact("execution_record", "application/json", _record(run)),
                        ExecutorArtifact("candidate_record", "application/json", _candidate(run)),
                        ExecutorArtifact("execution_note", "text/plain", b"Recorded note."),
                    ),
                )
            return ScientificExecutionResult(
                "completed", (ExecutorArtifact("argv", "text/plain", b"no"),)
            )

    executor = Watching()
    service, store, _authorizations, granted, _runner = _service(
        tmp_path, "sqlite", enabled=True, executor=executor
    )
    store_holder["store"] = store
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    completed = service.process_next()
    assert seen == ["running"]
    assert completed.status == "completed"
    assert completed.scientific_execution is True
    assert completed.scientific_validation is False
    assert completed.ranked is False
    assert completed.accepted is False
    assert completed.scientific_candidate_generated is True
    assert completed.text == COMPLETED_TEXT
    artifact_id = completed.artifacts[0].artifact_id
    record = service.read_artifact(
        session_id=SESSION, run_id=completed.run_id, artifact_id=artifact_id
    )
    assert record.content == _record(completed)
    assert service.process_next() is None
    refused = service.retry(session_id=SESSION, run_id=completed.run_id, idempotency_key="nope")
    assert refused.status == "retry_not_allowed"
    store._conn.execute(
        "UPDATE artifacts SET content = ? WHERE artifact_id = ?",
        (b"mutated", artifact_id),
    )
    broken = service.read_artifact(
        session_id=SESSION, run_id=completed.run_id, artifact_id=artifact_id
    )
    assert broken.status == "artifact_digest_mismatch"
    other = service.read_artifact(
        session_id="session_other", run_id=completed.run_id, artifact_id=artifact_id
    )
    assert other.status == "run_absent"
    assert executor.calls == 1
    assert created.run.run_id == completed.run_id
    store.close()


def test_invalid_candidate_bytes_do_not_mark_a_candidate(tmp_path: Path) -> None:
    class BadCandidate(RecordingExecutor):
        def execute(self, run):
            self.calls += 1
            return ScientificExecutionResult(
                "completed",
                (
                    ExecutorArtifact("execution_record", "application/json", _record(run)),
                    ExecutorArtifact(
                        "candidate_record", "application/json", b'{"schema":"SECRET-CANDIDATE"}'
                    ),
                ),
            )

    executor = BadCandidate()
    service, store, _authorizations, granted, _runner = _service(
        tmp_path, "sqlite", enabled=True, executor=executor
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    failed = service.process_next()
    assert executor.calls == 1
    assert failed.status == "failed"
    assert failed.scientific_candidate_generated is False
    assert failed.scientific_validation is False
    assert failed.scientific_execution is False
    assert failed.ranked is False
    assert failed.accepted is False
    assert failed.artifacts == ()
    published = handle_scientific_run(
        service, "GET", f"{RUNS_PATH}/{created.run.run_id}", b"", SESSION
    )
    body = json.dumps(published[1])
    assert published[1]["run"]["scientific_candidate_generated"] is False
    assert "SECRET-CANDIDATE" not in body
    assert "Traceback" not in body
    store.close()


def test_bad_outputs_fail_without_storing_artifacts(tmp_path: Path, backend: str) -> None:
    executor = RecordingExecutor(
        ScientificExecutionResult(
            "completed", (ExecutorArtifact("execution_note", "text/plain", b"/tmp/secret"),)
        )
    )
    service, store, _authorizations, granted, _runner = _service(
        tmp_path, backend, enabled=True, executor=executor
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    failed = service.process_next()
    assert failed.status == "failed"
    assert failed.text == FAILED_TEXT
    assert failed.artifacts == ()
    assert failed.scientific_candidate_generated is False
    missing = service.read_artifact(
        session_id=SESSION,
        run_id=created.run.run_id,
        artifact_id=f"artifact_{created.run.run_id}_1_execution_record",
    )
    assert missing.status == "artifact_absent"
    store.close()


def test_retry_snapshot_and_cross_attempt_artifact(tmp_path: Path, backend: str) -> None:
    calls = {"n": 0}

    class Once(RecordingExecutor):
        def execute(self, run):
            self.calls += 1
            calls["n"] += 1
            if calls["n"] == 1:
                return ScientificExecutionResult("failed", ())
            if calls["n"] == 2:
                return ScientificExecutionResult(
                    "completed",
                    (ExecutorArtifact("execution_record", "application/json", _record(run)),),
                )
            return ScientificExecutionResult(
                "completed",
                (ExecutorArtifact("execution_record", "application/json", _record(run)),),
            )

    service, store, _authorizations, granted, _runner = _service(
        tmp_path, backend, enabled=True, executor=Once()
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    first = service.process_next()
    assert first.status == "failed"
    queued = service.retry(session_id=SESSION, run_id=first.run_id, idempotency_key="retry-a")
    assert queued.run.attempt == 2
    assert queued.text == RETRY_QUEUED_TEXT
    second = service.process_next()
    assert second.status == "completed"
    assert second.attempt == 2
    visible = second.artifacts[0].artifact_id
    older = f"artifact_{first.run_id}_1_execution_record"
    assert service.read_artifact(
        session_id=SESSION, run_id=first.run_id, artifact_id=older
    ).status == ("artifact_absent")
    replay = service.retry(session_id=SESSION, run_id=first.run_id, idempotency_key="retry-a")
    assert replay.run.attempt == 2
    assert replay.run.status == "completed"
    later = service.retry(session_id=SESSION, run_id=first.run_id, idempotency_key="retry-b")
    assert later.status == "retry_not_allowed"
    assert service.read_artifact(
        session_id=SESSION, run_id=first.run_id, artifact_id=visible
    ).content
    assert created.run.run_id == second.run_id
    store.close()


def test_binding_change_blocks_execution(tmp_path: Path, backend: str) -> None:
    executor = RecordingExecutor()
    service, store, authorizations, granted, _runner = _service(
        tmp_path, backend, enabled=True, executor=executor
    )
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    shifted, _probe = _ready_readiness(
        tmp_path / "shift", when=datetime(2026, 10, 12, 18, 0, tzinfo=UTC)
    )
    authorizations._readiness = shifted
    failed = service.process_next()
    assert failed.status == "failed"
    assert failed.text == FAILED_TEXT
    assert executor.calls == 0
    store.close()


def test_queued_survives_restart_and_running_is_interrupted(tmp_path: Path) -> None:
    service, store, authorizations, granted, _runner = _service(tmp_path, "sqlite", enabled=True)
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    path = tmp_path / "scientific-runs.sqlite3"
    store.close()
    reopened = SQLiteScientificRunStore(path)
    restored = ScientificRunService(
        authorizations,
        store=reopened,
        executor=RecordingExecutor(),
        execution_enabled=True,
        clock=lambda: WHEN,
        run_ids=lambda: "run_" + "cd" * 16,
    )
    queued = restored.read(session_id=SESSION, run_id=created.run.run_id)
    assert queued.run.status == "queued"
    running = reopened.claim_next_queued(recorded_at=WHEN.isoformat())
    assert running.status == "running"
    reopened.close()
    recovered_store = SQLiteScientificRunStore(path)
    recovered = ScientificRunService(
        authorizations,
        store=recovered_store,
        executor=RecordingExecutor(),
        execution_enabled=True,
        clock=lambda: WHEN,
    )
    interrupted = recovered.startup()
    assert interrupted[0].status == "failed"
    assert interrupted[0].text == INTERRUPTED_TEXT
    assert recovered.process_next() is None
    observed = recovered.read(session_id=SESSION, run_id=created.run.run_id)
    assert observed.run.events[-1].text == INTERRUPTED_TEXT
    assert observed.run.events[-1].origin == "server"
    recovered_store.close()


def test_worker_is_separate_and_recovers_before_polling(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    service, store, _authorizations, granted, _runner = _service(
        tmp_path, "memory", enabled=True, executor=executor
    )
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    worker = ScientificRunWorker(service, poll_seconds=0.25)
    assert worker.run_once() is True
    assert executor.calls == 1
    assert worker.run_once() is False
    worker.start()
    assert worker.stop() is True
    assert type(worker).__name__ == "ScientificRunWorker"
    store.close()


def test_transport_rejects_client_control_and_stays_read_only(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    service, store, _authorizations, granted, _runner = _service(
        tmp_path, "memory", enabled=True, executor=executor
    )
    body = json.dumps(
        {
            "authorization_id": granted.authorization.authorization_id,
            "idempotency_key": "edge-1",
        }
    ).encode()
    status, payload = handle_scientific_run(service, "POST", RUNS_PATH, body, SESSION)
    assert status == 202
    assert payload["status"] == "run_queued"
    assert payload["scientific_validation"] is False
    run_id = payload["run"]["run_id"]
    for extra in (
        {"argv": ["bindcraft"]},
        {"command": "bindcraft"},
        {"environment": {"PATH": "/tmp"}},
        {"executable": "/tmp/bindcraft"},
        {"image_digest": "sha256:" + "ab" * 32},
        {"session_id": "session_other"},
        {"target_scope": "CD22"},
        {"working_directory": "/tmp"},
    ):
        rejected, rejected_body = handle_scientific_run(
            service,
            "POST",
            RUNS_PATH,
            json.dumps({**json.loads(body), **extra}).encode(),
            SESSION,
        )
        assert rejected == 400
        assert rejected_body["run"] is None
    queried, _query_body = handle_scientific_run(
        service, "GET", f"{RUNS_PATH}?session_id=other", b"", SESSION
    )
    assert queried == 400
    put, put_body = handle_scientific_run(service, "PUT", RUNS_PATH, body, SESSION)
    assert put == 404
    assert put_body["run"] is None
    assert executor.calls == 0
    read_status, observed = handle_scientific_run(
        service, "GET", f"{RUNS_PATH}/{run_id}", b"", SESSION
    )
    assert read_status == 200
    assert observed["run"]["status"] == "queued"
    assert executor.calls == 0
    foreign, _foreign_body = handle_scientific_run(
        service, "GET", f"{RUNS_PATH}/{run_id}", b"", "session_other"
    )
    assert foreign == 404
    finished = service.process_next()
    artifact_id = finished.artifacts[0].artifact_id
    artifact = handle_scientific_run(
        service, "GET", f"{RUNS_PATH}/{run_id}/artifacts/{artifact_id}", b"", SESSION
    )
    assert artifact.status == 200
    assert artifact.body == _record(finished)
    hidden = handle_scientific_run(
        service, "GET", f"{RUNS_PATH}/{run_id}/artifacts/{artifact_id}", b"", "session_other"
    )
    assert hidden[0] == 404
    assert executor.calls == 1
    store.close()


def test_configuration_fails_closed_before_a_server(tmp_path: Path, monkeypatch) -> None:
    database = tmp_path / "scientific-runs.sqlite3"
    with pytest.raises(ScientificRunConfigurationError) as unknown:
        resolve_scientific_run_backend({"CELLERTZ_SCIENTIFIC_RUN_STORE": "postgres"})
    assert unknown.value.code == "invalid_scientific_run_store"
    assert str(unknown.value) == unknown.value.code
    with pytest.raises(ScientificRunConfigurationError) as missing:
        open_scientific_run_store({"CELLERTZ_SCIENTIFIC_RUN_STORE": "sqlite"})
    assert missing.value.code == "scientific_run_db_required"
    with pytest.raises(ScientificRunConfigurationError) as execution:
        resolve_scientific_run_execution({"CELLERTZ_SCIENTIFIC_RUN_EXECUTION": "bindcraft"})
    assert execution.value.code == "invalid_scientific_run_execution"
    with pytest.raises(ScientificRunConfigurationError) as poll:
        resolve_scientific_run_worker_poll_seconds(
            {"CELLERTZ_SCIENTIFIC_RUN_WORKER_POLL_SECONDS": "0"}
        )
    assert poll.value.code == "invalid_scientific_run_worker_poll_seconds"
    assert resolve_scientific_run_execution({}) == "disabled"
    assert not database.exists()
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_RUN_EXECUTION", "enabled")

    def started(*_args, **_kwargs):
        raise AssertionError("server started")

    monkeypatch.setattr("cellertz.conversation.__main__.serve_conversation", started)
    with pytest.raises(ScientificRunConfigurationError) as blocked:
        main()
    assert blocked.value.code == "scientific_run_executor_unconfigured"
    assert not database.exists()
    assert list(tmp_path.iterdir()) == []


def test_mixed_durability_fails_before_a_server(tmp_path: Path, monkeypatch) -> None:
    auth_db = tmp_path / "authorizations.sqlite3"
    run_db = tmp_path / "scientific-runs.sqlite3"
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_AUTHORIZATION_STORE", "sqlite")
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_AUTHORIZATION_DB", str(auth_db))
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_RUN_STORE", "memory")

    def started(*_args, **_kwargs):
        raise AssertionError("server started")

    monkeypatch.setattr("cellertz.conversation.__main__.serve_conversation", started)
    with pytest.raises(ScientificRunConfigurationError) as mismatch:
        main()
    assert mismatch.value.code == "scientific_store_durability_mismatch"
    assert not auth_db.exists()
    assert not run_db.exists()
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_AUTHORIZATION_STORE", "memory")
    monkeypatch.delenv("CELLERTZ_SCIENTIFIC_AUTHORIZATION_DB", raising=False)
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_RUN_STORE", "sqlite")
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_RUN_DB", str(run_db))
    with pytest.raises(ScientificRunConfigurationError) as reverse:
        main()
    assert reverse.value.code == "scientific_store_durability_mismatch"
    assert not run_db.exists()


def test_mutated_public_run_and_artifact_filename_do_not_leak(tmp_path: Path) -> None:
    from cellertz.application.scientific_run import ScientificArtifactRecord

    service, store, _authorizations, granted, _runner = _service(tmp_path, "memory")
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    original = service.read

    def leak(**kwargs):
        reply = original(**kwargs)
        object.__setattr__(reply.run, "runtime_version", "/Users/fabio/secret")
        return reply

    service.read = leak
    status, payload = handle_scientific_run(
        service, "GET", f"{RUNS_PATH}/{created.run.run_id}", b"", SESSION
    )
    encoded = json.dumps(payload)
    assert status == 200
    assert payload["status"] == "adapter_failure"
    assert "/Users/fabio/secret" not in encoded
    assert "runtime_version" not in encoded

    def shadowed(**kwargs):
        reply = original(**kwargs)
        object.__setattr__(reply.run, "scientific_validation", True)
        object.__setattr__(reply.run, "scientific_candidate_generated", True)
        object.__setattr__(reply.run, "ranked", True)
        object.__setattr__(reply.run, "accepted", True)
        object.__setattr__(reply.run, "budget", {**reply.run.budget, "currency": "token=secret"})
        return reply

    service.read = shadowed
    shadowed_status, shadowed_payload = handle_scientific_run(
        service, "GET", f"{RUNS_PATH}/{created.run.run_id}", b"", SESSION
    )
    shadowed_body = json.dumps(shadowed_payload)
    assert shadowed_status == 200
    assert shadowed_payload["status"] == "adapter_failure"
    assert shadowed_payload["run"] is None
    assert shadowed_payload["scientific_validation"] is False
    assert shadowed_payload["scientific_candidate_generated"] is False
    assert shadowed_payload["ranked"] is False
    assert shadowed_payload["accepted"] is False
    assert "token=secret" not in shadowed_body
    service.read = original

    def bad_name(**_kwargs):
        return ScientificArtifactRecord(
            "text/plain",
            "scientific-execution-note.txt\r\nX-Evil: 1",
            b"hello",
        )

    service.read_artifact = bad_name
    rejected, body = handle_scientific_run(
        service,
        "GET",
        f"{RUNS_PATH}/{created.run.run_id}/artifacts/artifact_{created.run.run_id}_1_execution_note",
        b"",
        SESSION,
    )
    rejected_body = json.dumps(body)
    assert rejected == 409
    assert "X-Evil" not in rejected_body
    assert "\r" not in rejected_body

    class SubRecord(ScientificArtifactRecord):
        pass

    service.read_artifact = lambda **_kwargs: SubRecord(
        "text/plain", "scientific-execution-note.txt", b"hello"
    )
    subclass_status, subclass_body = handle_scientific_run(
        service,
        "GET",
        f"{RUNS_PATH}/{created.run.run_id}/artifacts/artifact_{created.run.run_id}_1_execution_note",
        b"",
        SESSION,
    )
    assert "scientific-execution-note" not in json.dumps(subclass_body)
    assert subclass_status == 200
    store.close()


def test_sqlite_artifact_read_rejects_a_changed_kind(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    service, store, _authorizations, granted, _runner = _service(
        tmp_path, "sqlite", enabled=True, executor=executor
    )
    service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    finished = service.process_next()
    artifact = finished.artifacts[0]
    store._conn.execute(
        "UPDATE artifacts SET kind = ? WHERE artifact_id = ?",
        ("candidate_record", artifact.artifact_id),
    )
    rejected = service.read_artifact(
        session_id=SESSION, run_id=finished.run_id, artifact_id=artifact.artifact_id
    )
    assert rejected.status == "artifact_media_type_rejected"
    encoded = json.dumps(rejected.to_dict())
    assert "Traceback" not in encoded
    assert "SELECT" not in encoded
    hidden = handle_scientific_run(
        service,
        "GET",
        f"{RUNS_PATH}/{finished.run_id}/artifacts/{artifact.artifact_id}",
        b"",
        SESSION,
    )
    assert hidden[0] == 409
    assert "Traceback" not in json.dumps(hidden[1])
    assert "candidate_record" not in json.dumps(hidden[1])
    store.close()


def test_sqlite_artifact_read_rejects_bad_blobs(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    service, store, _authorizations, granted, _runner = _service(
        tmp_path, "sqlite", enabled=True, executor=executor
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    executor.result = ScientificExecutionResult(
        "completed",
        (
            ExecutorArtifact("execution_record", "application/json", _record(created.run)),
            ExecutorArtifact("candidate_record", "application/json", _candidate(created.run)),
            ExecutorArtifact("execution_note", "text/plain", b"Observed note."),
        ),
    )
    finished = service.process_next()
    by_kind = {item.kind: item for item in finished.artifacts}

    def plant(artifact_id: str, content: bytes) -> None:
        digest = hashlib.sha256(content).hexdigest()
        document = store._conn.execute(
            "SELECT document FROM runs WHERE run_id = ?", (finished.run_id,)
        ).fetchone()["document"]
        current = run_from_document(document, confirmed=True)
        updated = replace(
            current,
            artifacts=tuple(
                replace(item, sha256=digest, size=len(content))
                if item.artifact_id == artifact_id
                else item
                for item in current.artifacts
            ),
        )
        store._conn.execute(
            "UPDATE runs SET document = ? WHERE run_id = ?",
            (run_document(updated), finished.run_id),
        )
        store._conn.execute(
            "UPDATE artifacts SET content = ?, sha256 = ?, size = ? WHERE artifact_id = ?",
            (content, digest, len(content), artifact_id),
        )

    def rejected(artifact_id: str) -> str:
        reply = service.read_artifact(
            session_id=SESSION, run_id=finished.run_id, artifact_id=artifact_id
        )
        encoded = json.dumps(reply.to_dict())
        assert "Traceback" not in encoded
        assert "SELECT" not in encoded
        return reply.status

    store._conn.execute(
        "UPDATE artifacts SET content = ? WHERE artifact_id = ?",
        ("SECRET-TEXT-BLOB", by_kind["execution_note"].artifact_id),
    )
    assert rejected(by_kind["execution_note"].artifact_id) == "artifact_invalid"
    assert "SECRET-TEXT-BLOB" not in json.dumps(
        service.read_artifact(
            session_id=SESSION,
            run_id=finished.run_id,
            artifact_id=by_kind["execution_note"].artifact_id,
        ).to_dict()
    )

    store._conn.execute(
        "UPDATE artifacts SET content = ? WHERE artifact_id = ?",
        (b"Q" * 65537, by_kind["execution_note"].artifact_id),
    )
    assert rejected(by_kind["execution_note"].artifact_id) == "artifact_too_large"

    store._conn.execute(
        "UPDATE artifacts SET sha256 = ? WHERE artifact_id = ?",
        ("ff" * 32, by_kind["execution_record"].artifact_id),
    )
    assert rejected(by_kind["execution_record"].artifact_id) == "artifact_digest_mismatch"

    plant(by_kind["execution_record"].artifact_id, b'{"schema":"SECRET-RECORD"}')
    assert rejected(by_kind["execution_record"].artifact_id) == "artifact_invalid"
    plant(by_kind["candidate_record"].artifact_id, b'{"schema":"SECRET-CANDIDATE"}')
    assert rejected(by_kind["candidate_record"].artifact_id) == "artifact_invalid"
    plant(by_kind["execution_note"].artifact_id, b"\x00SECRET-NOTE")
    note = service.read_artifact(
        session_id=SESSION,
        run_id=finished.run_id,
        artifact_id=by_kind["execution_note"].artifact_id,
    )
    encoded = json.dumps(note.to_dict())
    assert note.status == "artifact_invalid"
    assert "SECRET-NOTE" not in encoded
    assert "Traceback" not in encoded

    store._conn.execute(
        "DELETE FROM artifacts WHERE artifact_id = ?",
        (by_kind["candidate_record"].artifact_id,),
    )
    missing = handle_scientific_run(
        service,
        "GET",
        f"{RUNS_PATH}/{finished.run_id}/artifacts/{by_kind['candidate_record'].artifact_id}",
        b"",
        SESSION,
    )
    assert missing[0] == 404
    assert missing[1]["status"] == "artifact_absent"
    assert "Traceback" not in json.dumps(missing[1])
    store.close()


def test_safe_failure_is_persisted_when_the_store_returns(tmp_path: Path) -> None:
    service, store, _authorizations, granted, _runner = _service(tmp_path, "memory")
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    real = store.commit_transition
    calls = {"count": 0}

    def flaky(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] <= 2:
            raise ScientificRunStoreError("scientific_run_store_unavailable")
        return real(*args, **kwargs)

    store.commit_transition = flaky
    worker = ScientificRunWorker(service, poll_seconds=0.25)
    assert worker.run_once() is False
    assert store.read_run(SESSION, created.run.run_id).status == "running"
    assert worker.run_once() is True
    failed = store.read_run(SESSION, created.run.run_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.text == FAILED_TEXT
    assert store.read_run(SESSION, created.run.run_id).status == "failed"
    store.close()


def test_lost_safe_commit_retries_after_a_failed_read(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    service, store, _authorizations, granted, _runner = _service(
        tmp_path, "memory", enabled=True, executor=executor
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    real_commit = store.commit_transition
    real_read = store.read_run
    state = {"commits": 0, "read_failed": False}

    def commit(*args, **kwargs):
        state["commits"] += 1
        if state["commits"] == 1:
            raise ScientificRunStoreError("scientific_run_store_unavailable")
        if state["commits"] == 2:
            return "lost"
        return real_commit(*args, **kwargs)

    def read(*args, **kwargs):
        if state["commits"] == 2 and not state["read_failed"]:
            state["read_failed"] = True
            raise ScientificRunStoreError("scientific_run_store_unavailable")
        return real_read(*args, **kwargs)

    store.commit_transition = commit
    store.read_run = read
    worker = ScientificRunWorker(service, poll_seconds=0.25)
    assert worker.run_once() is False
    assert executor.calls == 1
    assert service._pending_failure is not None
    assert real_read(SESSION, created.run.run_id).status == "running"
    held = handle_scientific_run(service, "GET", f"{RUNS_PATH}/{created.run.run_id}", b"", SESSION)
    held_body = json.dumps(held[1])
    assert held[1]["run"]["status"] == "running"
    assert held[1]["run"]["scientific_execution"] is False
    assert held[1]["status"] != "run_observed" or held[1]["run"]["status"] != "completed"
    for leak in ("Traceback", "SELECT", "sqlite", str(tmp_path)):
        assert leak not in held_body
    assert worker.run_once() is True
    assert executor.calls == 1
    failed = real_read(SESSION, created.run.run_id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.text == FAILED_TEXT
    published = handle_scientific_run(
        service, "GET", f"{RUNS_PATH}/{created.run.run_id}", b"", SESSION
    )
    published_body = json.dumps(published[1])
    assert published[1]["run"]["status"] == "failed"
    assert published[1]["run"]["scientific_execution"] is False
    assert published[1]["text"] == FAILED_TEXT
    for leak in ("Traceback", "SELECT", "sqlite", str(tmp_path)):
        assert leak not in published_body
    store.close()


def test_divergent_timestamps_do_not_leave_the_store(tmp_path: Path) -> None:
    service, store, _authorizations, granted, _runner = _service(tmp_path, "sqlite")
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    original = service.read

    def shifted(**kwargs):
        reply = original(**kwargs)
        object.__setattr__(reply.run, "updated_at", "2026-10-12T18:00:00+00:00")
        return reply

    service.read = shifted
    status, payload = handle_scientific_run(
        service, "GET", f"{RUNS_PATH}/{created.run.run_id}", b"", SESSION
    )
    encoded = json.dumps(payload)
    assert status == 200
    assert payload["status"] == "adapter_failure"
    assert "2026-10-12T18:00:00+00:00" not in encoded
    assert "Traceback" not in encoded
    service.read = original

    queued = json.loads(
        store._conn.execute(
            "SELECT document FROM runs WHERE run_id = ?", (created.run.run_id,)
        ).fetchone()["document"]
    )
    document = json.loads(json.dumps(queued))
    document["created_at"] = "2026-10-12T17:00:00+00:00"
    store._conn.execute(
        "UPDATE runs SET document = ?, created_at = ? WHERE run_id = ?",
        (_canonical(document), document["created_at"], created.run.run_id),
    )
    hidden = handle_scientific_run(
        service, "GET", f"{RUNS_PATH}/{created.run.run_id}", b"", SESSION
    )
    hidden_body = json.dumps(hidden[1])
    assert hidden[0] == 409
    assert hidden[1]["status"] == "run_record_invalid"
    assert hidden[1]["run"] is None
    assert "2026-10-12T17:00:00+00:00" not in hidden_body
    assert "Traceback" not in hidden_body
    first = queued["events"][0]
    later = "2026-10-12T16:00:02+00:00"
    earlier = "2026-10-12T15:59:59+00:00"
    reversed_history = json.loads(json.dumps(queued))
    reversed_history["status"] = "failed"
    reversed_history["text"] = FAILED_TEXT
    reversed_history["updated_at"] = later
    reversed_history["events"] = [
        first,
        {
            **first,
            "sequence": 2,
            "status": "running",
            "text": RUNNING_TEXT,
            "recorded_at": earlier,
        },
        {
            **first,
            "sequence": 3,
            "status": "failed",
            "text": FAILED_TEXT,
            "recorded_at": later,
        },
    ]
    store._conn.execute(
        "UPDATE runs SET document = ?, status = ?, created_at = ?, updated_at = ? WHERE run_id = ?",
        (
            _canonical(reversed_history),
            "failed",
            reversed_history["created_at"],
            later,
            created.run.run_id,
        ),
    )
    backwards = handle_scientific_run(
        service, "GET", f"{RUNS_PATH}/{created.run.run_id}", b"", SESSION
    )
    backwards_body = json.dumps(backwards[1])
    assert backwards[0] == 409
    assert backwards[1]["status"] == "run_record_invalid"
    assert backwards[1]["run"] is None
    assert earlier not in backwards_body
    assert "Traceback" not in backwards_body
    assert "SELECT" not in backwards_body
    store.close()


def test_unstopped_scientific_worker_keeps_the_authorization_store(
    tmp_path: Path, monkeypatch
) -> None:
    auth_db = tmp_path / "authorizations.sqlite3"
    run_db = tmp_path / "scientific-runs.sqlite3"
    approved_db = tmp_path / "approved-runs.sqlite3"
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_AUTHORIZATION_STORE", "sqlite")
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_AUTHORIZATION_DB", str(auth_db))
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_RUN_STORE", "sqlite")
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_RUN_DB", str(run_db))
    monkeypatch.setenv("CELLERTZ_APPROVED_RUN_STORE", "sqlite")
    monkeypatch.setenv("CELLERTZ_APPROVED_RUN_DB", str(approved_db))
    opened: list[object] = []
    from cellertz.application.approved_run_config import open_approved_run_store
    from cellertz.application.scientific_authorization_config import (
        open_scientific_authorization_store,
    )
    from cellertz.application.scientific_run_config import open_scientific_run_store as open_runs

    def capture_auth(*args, **kwargs):
        store = open_scientific_authorization_store(*args, **kwargs)
        opened.append(store)
        return store

    def capture_runs(*args, **kwargs):
        store = open_runs(*args, **kwargs)
        opened.append(store)
        return store

    def capture_approved(*args, **kwargs):
        store = open_approved_run_store(*args, **kwargs)
        opened.append(store)
        return store

    class Stuck:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self, timeout: float = 5.0) -> bool:
            del timeout
            return False

    monkeypatch.setattr(
        "cellertz.conversation.__main__.open_scientific_authorization_store", capture_auth
    )
    monkeypatch.setattr("cellertz.conversation.__main__.open_scientific_run_store", capture_runs)
    monkeypatch.setattr("cellertz.conversation.__main__.open_approved_run_store", capture_approved)
    monkeypatch.setattr("cellertz.conversation.__main__.ScientificRunWorker", Stuck)
    monkeypatch.setattr("cellertz.conversation.__main__.serve_conversation", lambda *_a, **_k: None)
    main()
    from tests.test_approved_run_durability import _probe as approved_lock_probe
    from tests.test_scientific_authorization import _lock_probe as auth_lock_probe

    busy = auth_lock_probe(auth_db)
    assert busy.returncode == 0
    assert busy.stdout == "scientific_authorization_store_busy"
    held = approved_lock_probe(approved_db)
    assert held.returncode == 0
    assert held.stdout == "approved_run_store_busy"
    scientific_held = _lock_probe(run_db)
    assert scientific_held.returncode == 0
    assert scientific_held.stdout == "scientific_run_store_busy"
    for store in opened:
        store.close()


def test_stopped_workers_release_store_locks_once(tmp_path: Path, monkeypatch) -> None:
    auth_db = tmp_path / "authorizations.sqlite3"
    run_db = tmp_path / "scientific-runs.sqlite3"
    approved_db = tmp_path / "approved-runs.sqlite3"
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_AUTHORIZATION_STORE", "sqlite")
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_AUTHORIZATION_DB", str(auth_db))
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_RUN_STORE", "sqlite")
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_RUN_DB", str(run_db))
    monkeypatch.setenv("CELLERTZ_APPROVED_RUN_STORE", "sqlite")
    monkeypatch.setenv("CELLERTZ_APPROVED_RUN_DB", str(approved_db))
    from cellertz.application.approved_run_config import open_approved_run_store
    from cellertz.application.scientific_authorization_config import (
        open_scientific_authorization_store,
    )
    from cellertz.application.scientific_run_config import open_scientific_run_store as open_runs

    counts = {"auth": 0, "scientific": 0, "approved": 0}
    opened: list[object] = []

    def counted(store, key: str):
        original = store.close

        def close() -> None:
            counts[key] += 1
            original()

        store.close = close
        opened.append(store)
        return store

    monkeypatch.setattr(
        "cellertz.conversation.__main__.open_scientific_authorization_store",
        lambda *args, **kwargs: counted(
            open_scientific_authorization_store(*args, **kwargs), "auth"
        ),
    )
    monkeypatch.setattr(
        "cellertz.conversation.__main__.open_scientific_run_store",
        lambda *args, **kwargs: counted(open_runs(*args, **kwargs), "scientific"),
    )
    monkeypatch.setattr(
        "cellertz.conversation.__main__.open_approved_run_store",
        lambda *args, **kwargs: counted(open_approved_run_store(*args, **kwargs), "approved"),
    )
    monkeypatch.setattr("cellertz.conversation.__main__.serve_conversation", lambda *_a, **_k: None)
    main()
    assert counts == {"auth": 1, "scientific": 1, "approved": 1}
    from tests.test_approved_run_durability import _probe as approved_lock_probe
    from tests.test_scientific_authorization import _lock_probe as auth_lock_probe

    assert auth_lock_probe(auth_db).stdout == "opened"
    assert approved_lock_probe(approved_db).stdout == "opened"
    assert _lock_probe(run_db).stdout == "opened"
    for store in opened:
        store.close()
    assert counts == {"auth": 2, "scientific": 2, "approved": 2}
    assert auth_lock_probe(auth_db).stdout == "opened"
    assert approved_lock_probe(approved_db).stdout == "opened"
    assert _lock_probe(run_db).stdout == "opened"


def test_stale_binding_hash_fails_closed(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    service, store, _authorizations, granted, _runner = _service(
        tmp_path, "sqlite", enabled=True, executor=executor
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    run_id = created.run.run_id
    original = store._conn.execute(
        "SELECT document, binding_hash FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()

    def rewrite(document: dict) -> None:
        store._conn.execute(
            "UPDATE runs SET document = ?, binding_hash = ? WHERE run_id = ?",
            (_canonical(document), document["binding_hash"], run_id),
        )

    document = json.loads(original["document"])
    document["request_id"] = "request_other"
    rewrite(document)
    worker = ScientificRunWorker(service, poll_seconds=0.25)
    assert worker.run_once() is False
    assert executor.calls == 0
    hidden = handle_scientific_run(service, "GET", f"{RUNS_PATH}/{run_id}", b"", SESSION)
    body = json.dumps(hidden[1])
    assert hidden[0] == 409
    assert hidden[1]["status"] == "run_record_invalid"
    assert hidden[1]["run"] is None
    assert "request_other" not in body
    assert "Traceback" not in body
    assert "SELECT" not in body
    rewrite(json.loads(original["document"]))

    fresh = service.read
    service.read = lambda **kwargs: _shift(
        fresh(**kwargs), "runtime_version", "replacement-runtime-1"
    )
    status, payload = handle_scientific_run(service, "GET", f"{RUNS_PATH}/{run_id}", b"", SESSION)
    encoded = json.dumps(payload)
    assert status == 200
    assert payload["status"] == "adapter_failure"
    assert "replacement-runtime-1" not in encoded
    assert "Traceback" not in encoded
    service.read = fresh

    for change in (
        {"approval_id": "approval_other"},
        {"image_digest": "sha256:" + "12" * 32},
        {"manifest_digest": "12" * 32},
        {"readiness_observation_id": "observation_" + "12" * 32},
        {"readiness_observed_at": "2026-10-12T18:00:00+00:00"},
        {"request_digest": "12" * 32},
        {"request_id": "request_other"},
        {"request_revision": 9},
        {"runtime_name": "other-runtime"},
        {"runtime_version": "other-runtime-1"},
        {"session_id": "session_other"},
        {"tool_version": "other-tool-1"},
    ):
        mutated = json.loads(original["document"])
        mutated.update(change)
        rewrite(mutated)
        rejected = handle_scientific_run(service, "GET", f"{RUNS_PATH}/{run_id}", b"", SESSION)
        rejected_body = json.dumps(rejected[1])
        assert rejected[0] == 409
        assert rejected[1]["run"] is None
        assert "Traceback" not in rejected_body
        assert "SELECT" not in rejected_body
        leaked = next(iter(change.values()))
        if type(leaked) is str:
            assert leaked not in rejected_body
    budget_base = json.loads(original["document"])["budget"]
    for name, value in (
        ("action_on_limit", "warn" if budget_base["action_on_limit"] == "stop" else "stop"),
        ("currency", "EUR" if budget_base["currency"] != "EUR" else "USD"),
        ("human_override_required", not budget_base["human_override_required"]),
        ("model_cost_ceiling", 7),
        ("scientific_tool_cost_ceiling", 7),
        ("token_ceiling", 9),
        ("warning_threshold", 0.25 if budget_base["warning_threshold"] != 0.25 else 0.5),
    ):
        mutated = json.loads(original["document"])
        mutated["budget"] = {**mutated["budget"], name: value}
        rewrite(mutated)
        rejected = handle_scientific_run(service, "GET", f"{RUNS_PATH}/{run_id}", b"", SESSION)
        assert rejected[1]["status"] == "run_record_invalid"
        assert rejected[1]["run"] is None
    divergent = json.loads(original["document"])
    divergent["binding_hash"] = "cd" * 32
    rewrite(divergent)
    mismatched = handle_scientific_run(service, "GET", f"{RUNS_PATH}/{run_id}", b"", SESSION)
    mismatched_body = json.dumps(mismatched[1])
    assert mismatched[0] == 409
    assert mismatched[1]["status"] == "run_record_invalid"
    assert "cd" * 32 not in mismatched_body
    assert "Traceback" not in mismatched_body
    store.close()


def test_reply_matrix_rejects_unknown_and_keeps_exact_conflicts(tmp_path: Path) -> None:
    service, _store, _authorizations, granted, _runner = _service(tmp_path, "memory")
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    service.read = lambda **_kwargs: ScientificRunReply("exploded", "synthetic boom", SESSION)
    status, payload = handle_scientific_run(
        service, "GET", f"{RUNS_PATH}/{created.run.run_id}", b"", SESSION
    )
    encoded = json.dumps(payload)
    assert status == 200
    assert payload["status"] == "adapter_failure"
    assert "exploded" not in encoded
    assert "synthetic" not in encoded
    service.read = lambda **_kwargs: ScientificRunReply(
        "idempotency_conflict", _KEY_TEXT, SESSION, created.run
    )
    conflict, conflict_body = handle_scientific_run(
        service, "GET", f"{RUNS_PATH}/{created.run.run_id}", b"", SESSION
    )
    assert conflict == 409
    assert conflict_body["status"] == "idempotency_conflict"
    assert conflict_body["text"] == _KEY_TEXT
    assert conflict_body["run"]["run_id"] == created.run.run_id
    service.read = lambda **_kwargs: ScientificRunReply(
        "idempotency_conflict", QUEUED_TEXT, SESSION, created.run
    )
    broken, broken_body = handle_scientific_run(
        service, "GET", f"{RUNS_PATH}/{created.run.run_id}", b"", SESSION
    )
    assert broken == 200
    assert broken_body["status"] == "adapter_failure"
    assert broken_body["run"] is None
    service.read = lambda **_kwargs: ScientificRunReply(
        "retry_not_allowed", _RETRY_TEXT, SESSION, created.run
    )
    allowed, allowed_body = handle_scientific_run(
        service, "GET", f"{RUNS_PATH}/{created.run.run_id}", b"", SESSION
    )
    assert allowed == 409
    assert allowed_body["status"] == "retry_not_allowed"
    assert allowed_body["text"] == _RETRY_TEXT
    assert allowed_body["run"]["run_id"] == created.run.run_id


def _shift(reply, name: str, value: str):
    object.__setattr__(reply.run, name, value)
    return reply


def test_retry_snapshot_must_match_its_row(tmp_path: Path) -> None:
    calls = {"n": 0}

    class FailTwice(RecordingExecutor):
        def execute(self, run):
            self.calls += 1
            calls["n"] += 1
            if calls["n"] < 3:
                return ScientificExecutionResult("failed", ())
            return ScientificExecutionResult(
                "completed",
                (ExecutorArtifact("execution_record", "application/json", _record(run)),),
            )

    service, store, _authorizations, granted, _runner = _service(
        tmp_path, "sqlite", enabled=True, executor=FailTwice()
    )
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    service.process_next()
    queued = service.retry(session_id=SESSION, run_id=created.run.run_id, idempotency_key="retry-a")
    assert queued.run.attempt == 2
    service.process_next()
    sealed = service.retry(session_id=SESSION, run_id=created.run.run_id, idempotency_key="retry-a")
    assert sealed.run.attempt == 2
    assert sealed.run.status == "failed"
    later = service.retry(session_id=SESSION, run_id=created.run.run_id, idempotency_key="retry-b")
    assert later.run.attempt == 3
    original = store._conn.execute(
        "SELECT snapshot FROM retry_keys WHERE idempotency_key = ?", ("retry-a",)
    ).fetchone()["snapshot"]
    other = store._conn.execute(
        "SELECT document FROM runs WHERE run_id = ?", (created.run.run_id,)
    ).fetchone()["document"]

    def plant(snapshot: str) -> None:
        store._conn.execute(
            "UPDATE retry_keys SET snapshot = ? WHERE idempotency_key = ?",
            (snapshot, "retry-a"),
        )

    def replay():
        return service.retry(
            session_id=SESSION, run_id=created.run.run_id, idempotency_key="retry-a"
        )

    plant(other)
    swapped = replay()
    encoded = json.dumps(swapped.to_dict())
    assert swapped.status == "run_record_invalid"
    assert swapped.run is None
    assert "Traceback" not in encoded
    assert "SELECT" not in encoded
    plant(original)
    assert replay().run.attempt == 2

    foreign = json.loads(original)
    foreign["run_id"] = "run_" + "cd" * 16
    plant(_canonical(foreign))
    assert replay().status == "run_record_invalid"
    foreign = json.loads(original)
    foreign["session_id"] = "session_other"
    plant(_canonical(foreign))
    assert replay().status == "run_record_invalid"
    foreign = json.loads(original)
    foreign["attempt"] = 3
    foreign["events"][-1]["attempt"] = 3
    plant(_canonical(foreign))
    assert replay().status == "run_record_invalid"
    foreign = json.loads(original)
    foreign["status"] = "queued"
    foreign["text"] = QUEUED_TEXT
    foreign["events"] = [foreign["events"][0]]
    foreign["updated_at"] = foreign["created_at"]
    plant(_canonical(foreign))
    assert replay().status == "run_record_invalid"
    plant(original + " ")
    assert replay().status == "run_record_invalid"
    store._conn.execute(
        "UPDATE retry_keys SET content_hash = 1 WHERE idempotency_key = ?", ("retry-a",)
    )
    typed = replay()
    typed_body = json.dumps(typed.to_dict())
    assert typed.status == "run_record_invalid"
    assert typed.run is None
    assert "Traceback" not in typed_body
    assert str(store._path) not in typed_body
    store.close()


def test_modules_do_not_name_execution_machinery() -> None:
    forbidden = (
        "ProteinSupervisorV1",
        "LocalProteinRunStarter",
        "subprocess",
        "shell=True",
        "Popen",
        "ScientificRunMemory",
        "Apptainer",
        "SecondBrain",
        "Databricks",
    )
    for path in _MODULES:
        source = path.read_text(encoding="utf-8")
        for name in forbidden:
            assert name not in source


def test_importing_modules_does_not_create_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    import cellertz.application.scientific_run as run_module
    import cellertz.application.scientific_run_config as config_module
    import cellertz.application.scientific_run_store as store_module

    assert run_module.QUEUED_TEXT
    assert config_module.MEMORY == "memory"
    assert store_module.SCHEMA_VERSION == 1
    assert list(tmp_path.iterdir()) == []


def test_symlink_fork_corruption_and_rollback(tmp_path: Path) -> None:
    path = tmp_path / "scientific-runs.sqlite3"
    service, store, authorizations, granted, _runner = _service(tmp_path, "sqlite")
    created = service.create(
        session_id=SESSION,
        authorization_id=granted.authorization.authorization_id,
        idempotency_key="run-1",
    )
    link = tmp_path / "alias.sqlite3"
    link.symlink_to(path)
    same = SQLiteScientificRunStore(link)
    same.close()
    busy = _lock_probe(link)
    assert busy.returncode == 0
    assert busy.stdout == "scientific_run_store_busy"
    assert "Traceback" not in busy.stderr
    store.close()
    forked = _fork_probe(path, SESSION, created.run.run_id)
    assert forked.returncode == 0
    assert forked.stdout == "scientific_run_store_busy"
    assert "Traceback" not in forked.stderr
    reopened = SQLiteScientificRunStore(path)
    reopened._conn.execute(
        "UPDATE runs SET document = ? WHERE run_id = ?",
        (b"\xff", created.run.run_id),
    )
    broken = ScientificRunService(authorizations, store=reopened, clock=lambda: WHEN)
    observed = broken.read(session_id=SESSION, run_id=created.run.run_id)
    assert observed.status == "run_record_invalid"
    encoded = json.dumps(observed.to_dict())
    for leak in ("Traceback", "sqlite", "SELECT", "/tmp", "\xff"):
        assert leak not in encoded
    reopened._fail_on = "retry_key"
    with pytest.raises(ScientificRunStoreError):
        reopened.claim_retry(
            run_id=created.run.run_id,
            session_id=SESSION,
            scope=(SESSION, "retry-broken"),
            content_hash="ab" * 32,
            expected_attempt=1,
            recorded_at=WHEN.isoformat(),
        )
    row = reopened._conn.execute(
        "SELECT status, attempt FROM runs WHERE run_id = ?",
        (created.run.run_id,),
    ).fetchone()
    assert row["status"] == "queued"
    assert row["attempt"] == 1
    reopened.close()


def test_handler_unsupported_method_does_not_execute(tmp_path: Path) -> None:
    executor = RecordingExecutor()
    service, store, _authorizations, granted, _runner = _service(
        tmp_path, "memory", enabled=True, executor=executor
    )
    handler = configured_handler(object(), session_id=SESSION, scientific_runs=service)
    status, payload = _invoke(handler, "PUT", RUNS_PATH, b"{}")
    assert status == 404
    assert payload["run"] is None
    assert executor.calls == 0
    assert service.list_runs(session_id=SESSION).runs == ()
    body = json.dumps(
        {
            "authorization_id": granted.authorization.authorization_id,
            "idempotency_key": "edge-1",
        }
    ).encode()
    created, created_body = _invoke(handler, "POST", RUNS_PATH, body)
    assert created == 202
    assert created_body["status"] == "run_queued"
    assert executor.calls == 0
    store.close()


def _invoke(handler: type, method: str, path: str, body: bytes) -> tuple[int, dict]:
    from io import BytesIO

    instance = handler.__new__(handler)
    instance.path = path
    instance.headers = {"Content-Length": str(len(body))}
    instance.rfile = BytesIO(body)
    instance.wfile = BytesIO()
    instance.send_response = lambda code: setattr(instance, "status_code", code)
    instance.send_header = lambda *_args: None
    instance.end_headers = lambda: None
    if method == "GET":
        instance.do_GET()
    elif method == "POST":
        instance.do_POST()
    elif method == "PUT":
        instance.do_PUT()
    else:
        instance.do_DELETE()
    instance.wfile.seek(0)
    return instance.status_code, json.loads(instance.wfile.getvalue())


def _lock_probe(path: Path) -> subprocess.CompletedProcess[str]:
    script = """
import sys
from pathlib import Path
from cellertz.application.scientific_run import ScientificRunStoreError
from cellertz.application.scientific_run_store import SQLiteScientificRunStore
try:
    opened = SQLiteScientificRunStore(Path(sys.argv[1]))
except ScientificRunStoreError as exc:
    sys.stdout.write(exc.code)
    raise SystemExit(0)
opened.close()
sys.stdout.write("opened")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-c", script, str(path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _fork_probe(path: Path, session_id: str, run_id: str) -> subprocess.CompletedProcess[str]:
    script = """
import os
import sys
from pathlib import Path
from cellertz.application.scientific_run import ScientificRunStoreError
from cellertz.application.scientific_run_store import SQLiteScientificRunStore

database, session_id, run_id = sys.argv[1:]
store = SQLiteScientificRunStore(Path(database))
current = store.read_run(session_id, run_id)
if current is None or current.status != "queued":
    sys.stdout.write("missing")
    store.close()
    raise SystemExit(0)
pid = os.fork()
if pid == 0:
    try:
        SQLiteScientificRunStore(Path(database))
    except ScientificRunStoreError as exc:
        os.write(1, exc.code.encode())
    else:
        os.write(1, b"opened")
    os._exit(0)
_child, status = os.waitpid(pid, 0)
again = store.read_run(session_id, run_id)
store.close()
if status != 0 or again is None or again.status != "queued":
    sys.stdout.write("changed")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-c", script, str(path), session_id, run_id],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
