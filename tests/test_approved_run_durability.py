"""Phase 6F durable operational store. Not scientific memory."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from cellertz.application.approved_run import (
    ApplicationContractError,
    ApprovedRun,
    ApprovedRunStoreError,
    FixtureApprovedRunRunner,
    run_from_document,
)
from cellertz.application.approved_run_config import (
    ApprovedRunConfigurationError,
    open_approved_run_store,
    resolve_approved_run_backend,
)
from cellertz.application.approved_run_store import (
    APPROVED_RUN_SCHEMA_VERSION,
    SQLiteApprovedRunStore,
)
from cellertz.application.approved_run_transport import handle_create_approved_run
from cellertz.application.request_draft import DesignDraftCommand
from cellertz.conversation.__main__ import main

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_approved_run import SESSION, WHEN, _finish, _service
from tests.test_human_approval import _decide, _stack

_INTERRUPTED = "The fixture run was interrupted before completion. No scientific action was taken."
_FAILURE = "The fixture run failed. No scientific action was taken."


def _ready(
    tmp_path: Path,
    name: str = "approved-runs.sqlite3",
    *,
    session_id: str = SESSION,
    prefix: str = "",
):
    gate, drafter, _store, _dispatcher, reply = _stack(session_id=session_id, id_prefix=prefix)
    recorded = _decide(gate, reply)
    assert recorded.approval is not None
    dispatched = gate.dispatch(
        session_id=session_id,
        approval_id=recorded.approval.approval_id,
        idempotency_key=f"dispatch-{prefix or 'a'}",
    )
    assert dispatched.status == "demonstration_completed"
    store = SQLiteApprovedRunStore(tmp_path / name)
    service, runner = _service(gate, store=store, start=len(prefix))
    return service, runner, store, gate, drafter, recorded.approval


def _reopen(path: Path, gate, runner=None, start: int = 0):
    store = SQLiteApprovedRunStore(path)
    service, adapter = _service(gate, runner=runner, store=store, start=start)
    return service, adapter, store


def test_completed_run_remains_readable_after_reopen(tmp_path: Path):
    service, runner, store, gate, _drafter, approval = _ready(tmp_path)
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    created = _finish(service, created)
    assert created.run is not None and created.run.status == "completed"
    document = created.run.to_dict()
    path = store._path
    store.close()
    reopened, fresh, again = _reopen(path, gate, start=20)
    observed = reopened.read(session_id=SESSION, run_id=document["run_id"])
    assert observed.status == "run_observed"
    assert observed.run is not None
    assert observed.run.to_dict() == document
    assert fresh.calls == 0
    assert runner.calls == 1
    again.close()


def test_failed_run_remains_readable_after_reopen(tmp_path: Path):
    service, _runner, store, gate, _drafter, approval = _ready(tmp_path)
    service._runner = FixtureApprovedRunRunner(RuntimeError("Traceback /tmp/secret TOKEN"))
    failed = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    failed = _finish(service, failed)
    assert failed.run is not None and failed.text == _FAILURE
    path = store._path
    run_id = failed.run.run_id
    store.close()
    reopened, fresh, again = _reopen(path, gate)
    observed = reopened.read(session_id=SESSION, run_id=run_id)
    assert observed.run is not None
    assert observed.run.status == "failed"
    assert observed.text == _FAILURE
    assert observed.run.to_dict()["text"] == _FAILURE
    assert fresh.calls == 0
    again.close()


def test_events_artifacts_budget_and_usage_survive_restart(tmp_path: Path):
    service, _runner, store, gate, _drafter, approval = _ready(tmp_path)
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    created = _finish(service, created)
    assert created.run is not None
    before = created.run
    path = store._path
    store.close()
    reopened, _fresh, again = _reopen(path, gate)
    observed = reopened.read(session_id=SESSION, run_id=before.run_id)
    assert observed.run is not None
    events = observed.run.events
    assert [event.sequence for event in events] == [1, 2, 3]
    assert [event.status for event in events] == ["queued", "running", "completed"]
    assert [event.origin for event in events] == ["server", "server", "server"]
    assert [event.attempt for event in events] == [1, 1, 1]
    assert [event.recorded_at for event in events] == [event.recorded_at for event in before.events]
    assert observed.run.artifacts == before.artifacts
    assert observed.run.budget == before.budget
    assert observed.run.usage == before.usage
    assert observed.run.usage["observed_model_calls"] == 0
    assert observed.run.usage["observed_scientific_tool_calls"] == 0
    again.close()


def test_create_key_replays_after_restart_and_conflicts_when_content_differs(tmp_path: Path):
    service, runner, store, gate, drafter, approval = _ready(tmp_path)
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="key-1"
    )
    created = _finish(service, created)
    assert created.run is not None
    path = store._path
    store.close()
    reopened, fresh, again = _reopen(path, gate, start=30)
    replay = reopened.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="key-1"
    )
    assert replay.run is not None
    assert replay.run.run_id == created.run.run_id
    assert fresh.calls == 0
    other = drafter.revise(
        DesignDraftCommand(SESSION, "design a binder for pdl1 using fixture with 2 candidates")
    )
    recorded = _decide(gate, other, idempotency_key="decision-b")
    assert recorded.approval is not None
    gate.dispatch(
        session_id=SESSION, approval_id=recorded.approval.approval_id, idempotency_key="dispatch-b"
    )
    conflict = reopened.create(
        session_id=SESSION,
        approval_id=recorded.approval.approval_id,
        idempotency_key="key-1",
    )
    assert conflict.status == "idempotency_conflict"
    assert fresh.calls == 0
    assert runner.calls == 1
    again.close()


def test_secondary_key_conflicts_for_another_approval_after_restart(tmp_path: Path):
    service, runner, store, gate, drafter, approval = _ready(tmp_path)
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="key-1"
    )
    created = _finish(service, created)
    bound = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="key-2"
    )
    assert created.run is not None and bound.run is not None
    assert bound.run.run_id == created.run.run_id
    path = store._path
    store.close()
    reopened, fresh, again = _reopen(path, gate, start=40)
    other = drafter.revise(
        DesignDraftCommand(SESSION, "design a binder for pdl1 using fixture with 2 candidates")
    )
    recorded = _decide(gate, other, idempotency_key="decision-b")
    assert recorded.approval is not None
    gate.dispatch(
        session_id=SESSION, approval_id=recorded.approval.approval_id, idempotency_key="dispatch-b"
    )
    conflict = reopened.create(
        session_id=SESSION,
        approval_id=recorded.approval.approval_id,
        idempotency_key="key-2",
    )
    assert conflict.status == "idempotency_conflict"
    assert fresh.calls == 0
    assert runner.calls == 1
    again.close()


def test_two_store_instances_cannot_create_two_runs_for_one_approval(tmp_path: Path):
    service, runner, store, gate, _drafter, approval = _ready(tmp_path)
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="key-1"
    )
    created = _finish(service, created)
    assert created.run is not None
    other_store = SQLiteApprovedRunStore(store._path)
    other, other_runner = _service(gate, store=other_store, start=50)
    replay = other.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="key-2"
    )
    assert replay.run is not None
    assert replay.run.run_id == created.run.run_id
    assert other_runner.calls == 0
    assert runner.calls == 1
    other_store.close()
    store.close()


def test_distinct_sessions_stay_isolated_in_one_database(tmp_path: Path):
    left, left_runner, left_store, _gate, _drafter, approval = _ready(tmp_path, prefix="a")
    other = "session_phase6d_other"
    right_gate, _drafter_b, _store_b, _dispatcher_b, reply_b = _stack(
        session_id=other, id_prefix="b"
    )
    recorded = _decide(right_gate, reply_b)
    assert recorded.approval is not None
    right_gate.dispatch(
        session_id=other, approval_id=recorded.approval.approval_id, idempotency_key="dispatch-b"
    )
    right_store = SQLiteApprovedRunStore(left_store._path)
    right, right_runner = _service(right_gate, store=right_store, start=60)
    first = left.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="same"
    )
    second = right.create(
        session_id=other, approval_id=recorded.approval.approval_id, idempotency_key="same"
    )
    assert first.run is not None and second.run is not None
    assert first.run.run_id != second.run.run_id
    assert left_runner.calls == 0 and right_runner.calls == 0
    assert left.process_next() is not None
    assert right.process_next() is not None
    assert left_runner.calls == 1 and right_runner.calls == 1
    assert right.read(session_id=other, run_id=first.run.run_id).status == "run_absent"
    left_store.close()
    right_store.close()


def test_concurrent_store_instances_call_the_runner_once(tmp_path: Path):
    gate, _drafter, _store, _dispatcher, reply = _stack(id_prefix="c")
    recorded = _decide(gate, reply)
    assert recorded.approval is not None
    gate.dispatch(
        session_id=SESSION, approval_id=recorded.approval.approval_id, idempotency_key="dispatch-c"
    )
    path = tmp_path / "approved-runs.sqlite3"
    stores = [SQLiteApprovedRunStore(path) for _ in range(4)]
    services = [_service(gate, store=store, start=70 + index) for index, store in enumerate(stores)]
    barrier = threading.Barrier(4)
    results: list = []

    def create(index: int) -> None:
        barrier.wait()
        service, _runner = services[index]
        results.append(
            service.create(
                session_id=SESSION,
                approval_id=recorded.approval.approval_id,
                idempotency_key=f"parallel-{index}",
            )
        )

    threads = [threading.Thread(target=create, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert {item.run.run_id for item in results} == {results[0].run.run_id}
    assert sum(runner.calls for _service_item, runner in services) == 0
    assert services[0][0].process_next() is not None
    assert sum(runner.calls for _service_item, runner in services) == 1
    for store in stores:
        store.close()


def test_concurrent_retries_increment_once_and_snapshots_survive_restart(tmp_path: Path):
    service, _runner, store, gate, _drafter, approval = _ready(tmp_path)
    service._runner = FixtureApprovedRunRunner(RuntimeError("Traceback /tmp/secret TOKEN"))
    failed = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    failed = _finish(service, failed)
    assert failed.run is not None and failed.run.attempt == 1
    path = store._path
    stores = [store, SQLiteApprovedRunStore(path)]
    services = [
        (service, FixtureApprovedRunRunner(RuntimeError("Traceback TOKEN"))),
        _service(
            gate,
            store=stores[1],
            runner=FixtureApprovedRunRunner(RuntimeError("Traceback TOKEN")),
            start=80,
        ),
    ]
    service._store = stores[0]
    service._runner = services[0][1]
    barrier = threading.Barrier(2)
    results: dict[int, object] = {}

    def retry(index: int) -> None:
        barrier.wait()
        current, _adapter = services[index]
        results[index] = current.retry(
            session_id=SESSION,
            run_id=failed.run.run_id,
            idempotency_key=f"retry-{index}",
        )

    threads = [threading.Thread(target=retry, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    winner = next(index for index, item in results.items() if item.status == "run_queued")
    assert services[0][0].process_next() is not None
    assert sum(adapter.calls for _current, adapter in services) == 1
    assert max(item.run.attempt for item in results.values()) == 2
    for item in stores:
        item.close()
    reopened, fresh, again = _reopen(path, gate, start=90)
    reopened._runner = FixtureApprovedRunRunner(RuntimeError("Traceback /tmp/secret TOKEN"))
    later = reopened.retry(
        session_id=SESSION, run_id=failed.run.run_id, idempotency_key="retry-later"
    )
    later = _finish(reopened, later)
    assert later.run is not None and later.run.attempt == 3 and later.run.status == "failed"
    replay = reopened.retry(
        session_id=SESSION, run_id=failed.run.run_id, idempotency_key=f"retry-{winner}"
    )
    assert replay.run is not None
    assert replay.run.attempt == 2
    assert replay.run.status == "failed"
    observed = reopened.read(session_id=SESSION, run_id=failed.run.run_id)
    assert observed.run is not None and observed.run.attempt == 3
    assert fresh.calls == 0
    again.close()


def test_retry_snapshot_stays_on_its_attempt_after_restart(tmp_path: Path):
    service, _runner, store, gate, _drafter, approval = _ready(tmp_path)
    failing = FixtureApprovedRunRunner(RuntimeError("Traceback /tmp/secret TOKEN"))
    service._runner = failing
    failed = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    failed = _finish(service, failed)
    assert failed.run is not None
    second = service.retry(session_id=SESSION, run_id=failed.run.run_id, idempotency_key="retry-1")
    second = _finish(service, second)
    third = service.retry(session_id=SESSION, run_id=failed.run.run_id, idempotency_key="retry-2")
    third = _finish(service, third)
    assert second.run is not None and third.run is not None
    assert second.run.attempt == 2 and third.run.attempt == 3
    path = store._path
    store.close()
    reopened, fresh, again = _reopen(path, gate)
    replay = reopened.retry(session_id=SESSION, run_id=failed.run.run_id, idempotency_key="retry-1")
    assert replay.run is not None
    assert replay.run.attempt == 2
    assert replay.run.status == "failed"
    assert [event.attempt for event in replay.run.events if event.status == "failed"] == [1, 2]
    observed = reopened.read(session_id=SESSION, run_id=failed.run.run_id)
    assert observed.run is not None and observed.run.attempt == 3
    assert fresh.calls == 0
    assert failing.calls == 3
    again.close()


def test_direct_reads_and_get_do_not_call_the_runner(tmp_path: Path):
    service, runner, store, gate, _drafter, approval = _ready(tmp_path)
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    assert created.run is not None
    before = runner.calls
    assert store.read_run(SESSION, created.run.run_id) is not None
    observed = service.read(session_id=SESSION, run_id=created.run.run_id)
    assert observed.status == "run_observed"
    assert runner.calls == before
    store.close()
    _reopened, fresh, again = _reopen(store._path, gate)
    again.read_run(SESSION, created.run.run_id)
    assert fresh.calls == 0
    again.close()


def test_queued_and_running_runs_recover_without_the_runner(tmp_path: Path):
    service, runner, store, gate, _drafter, approval = _ready(tmp_path)
    queued = service._queued_new(
        approval,
        SESSION,
        service._gate.recorded_demonstration(
            session_id=SESSION, approval_id=approval.approval_id
        ).budget,
    )
    with pytest.raises(ApplicationContractError):
        queued.transition("failed", recorded_at=WHEN.isoformat(), text=_INTERRUPTED)
    outcome, claimed = store.claim_create(
        scope=(SESSION, "queued-key"),
        content_hash="queued-hash",
        approval_key=(SESSION, approval.approval_id),
        run=queued,
    )
    assert outcome == "created"
    running = claimed.transition(
        "running", recorded_at=WHEN.isoformat(), text="Fixture runner started."
    )
    assert (
        store.commit_transition(running, expected_status="queued", expected_attempt=1)
        == "committed"
    )
    path = store._path
    store.close()
    reopened, fresh, again = _reopen(path, gate)
    recovered = reopened.recover_interrupted()
    assert len(recovered) == 1
    assert recovered[0].run_id == claimed.run_id
    assert recovered[0].status == "failed"
    assert recovered[0].attempt == 1
    assert recovered[0].events[-1].text == _INTERRUPTED
    assert recovered[0].events[-1].origin == "server"
    assert [event.status for event in recovered[0].events] == ["queued", "running", "failed"]
    again_recovered = reopened.recover_interrupted()
    assert again_recovered == ()
    observed = reopened.read(session_id=SESSION, run_id=claimed.run_id)
    assert observed.text == _INTERRUPTED
    assert len(observed.run.events) == 3
    retried = reopened.retry(
        session_id=SESSION, run_id=claimed.run_id, idempotency_key="retry-after-recovery"
    )
    assert retried.run is not None
    assert retried.run.run_id == claimed.run_id
    assert retried.run.attempt == 2
    assert retried.run.status == "queued"
    retried = _finish(reopened, retried)
    assert retried.run.status == "completed"
    assert runner.calls == 0
    assert fresh.calls == 1
    again.close()


def test_queued_run_survives_restart_and_is_processed_without_recovery(tmp_path: Path):
    service, runner, store, gate, _drafter, approval = _ready(tmp_path)
    demonstration = gate.recorded_demonstration(
        session_id=SESSION, approval_id=approval.approval_id
    )
    queued = service._queued_new(approval, SESSION, demonstration.budget)
    store.claim_create(
        scope=(SESSION, "only-queued"),
        content_hash="only-queued",
        approval_key=(SESSION, approval.approval_id),
        run=queued,
    )
    path = store._path
    store.close()
    reopened, fresh, again = _reopen(path, gate)
    first = reopened.recover_interrupted()
    second = reopened.recover_interrupted()
    assert first == ()
    assert second == ()
    observed = reopened.read(session_id=SESSION, run_id=queued.run_id)
    assert observed.run is not None and observed.run.status == "queued"
    completed = reopened.process_next()
    assert completed is not None and completed.status == "completed" and completed.attempt == 1
    assert runner.calls == 0
    assert fresh.calls == 1
    again.close()


def test_unsupported_schema_fails_closed_without_modification(tmp_path: Path):
    path = tmp_path / "approved-runs.sqlite3"
    store = SQLiteApprovedRunStore(path)
    store.close()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("UPDATE schema_meta SET version = 3")
    conn.commit()
    conn.close()
    for suffix in ("-wal", "-shm"):
        leftover = Path(str(path) + suffix)
        if leftover.exists():
            leftover.unlink()
    before = path.read_bytes()
    with pytest.raises(ApprovedRunStoreError) as caught:
        SQLiteApprovedRunStore(path)
    assert caught.value.code == "unsupported_approved_run_schema"
    assert path.read_bytes() == before
    checked = sqlite3.connect(path)
    assert checked.execute("SELECT version FROM schema_meta WHERE id = 1").fetchone()[0] == 3
    checked.close()
    assert APPROVED_RUN_SCHEMA_VERSION == 2


def test_corrupt_rows_fail_safely_without_the_runner(tmp_path: Path):
    service, runner, store, _gate, _drafter, approval = _ready(tmp_path)
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    assert created.run is not None
    store._conn.execute(
        "UPDATE runs SET document = ? WHERE run_id = ?",
        ('{"text": "Traceback SELECT /tmp/secret TOKEN"}', created.run.run_id),
    )
    observed = service.read(session_id=SESSION, run_id=created.run.run_id)
    encoded = json.dumps(observed.to_dict())
    assert observed.status == "run_record_invalid"
    assert observed.run is None
    for leak in ("Traceback", "SELECT", "/tmp/secret", "TOKEN", "sqlite", str(store._path)):
        assert leak not in encoded
    assert runner.calls == 0
    with pytest.raises(ApprovedRunStoreError):
        store.read_run(SESSION, created.run.run_id)
    store.close()


def test_a_failed_transaction_leaves_no_partial_rows(tmp_path: Path):
    service, runner, store, _gate, _drafter, approval = _ready(tmp_path)
    failing = FixtureApprovedRunRunner(RuntimeError("Traceback TOKEN"))
    service._runner = failing
    failed = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    assert failed.run is not None
    failed = _finish(service, failed)
    store._fail_on = "retry_key"
    service._runner = FixtureApprovedRunRunner()
    blocked = service.retry(
        session_id=SESSION, run_id=failed.run.run_id, idempotency_key="retry-partial"
    )
    assert blocked.status == "run_record_invalid"
    assert service._runner.calls == 0
    store._fail_on = None
    row = store._conn.execute("SELECT status, attempt FROM runs").fetchone()
    assert row["status"] == "failed" and row["attempt"] == 1
    assert store._conn.execute("SELECT COUNT(*) FROM retry_keys").fetchone()[0] == 0
    keys = store._conn.execute("SELECT COUNT(*) FROM create_keys").fetchone()[0]
    assert keys == 1

    store._fail_on = "create_key"
    demonstration = service._gate.recorded_demonstration(
        session_id=SESSION, approval_id=approval.approval_id
    )
    queued = replace(
        service._queued_new(approval, SESSION, demonstration.budget), approval_id="approval_other"
    )
    with pytest.raises(ApprovedRunStoreError):
        store.claim_create(
            scope=(SESSION, "partial-key"),
            content_hash="partial-hash",
            approval_key=(SESSION, "approval_other"),
            run=queued,
        )
    store._fail_on = None
    assert store._conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    assert store._conn.execute("SELECT COUNT(*) FROM create_keys").fetchone()[0] == 1
    store.close()
    assert runner.calls == 0


def test_invalid_configuration_does_not_create_files_or_start_the_server(
    tmp_path: Path, monkeypatch
):
    database = tmp_path / "approved-runs.sqlite3"
    monkeypatch.setenv("CELLERTZ_APPROVED_RUN_STORE", "postgres")
    monkeypatch.setenv("CELLERTZ_APPROVED_RUN_DB", str(database))

    def started(*_args, **_kwargs):
        raise AssertionError("server started")

    monkeypatch.setattr("cellertz.conversation.__main__.serve_conversation", started)
    with pytest.raises(ApprovedRunConfigurationError) as caught:
        main()
    assert caught.value.code == "invalid_approved_run_store"
    assert not database.exists()
    assert list(tmp_path.iterdir()) == []
    with pytest.raises(ApprovedRunConfigurationError) as missing:
        resolve_approved_run_backend({"CELLERTZ_APPROVED_RUN_STORE": "sqlite"})
    assert missing.value.code == "approved_run_db_required"
    assert open_approved_run_store({}).__class__.__name__ == "InMemoryApprovedRunStore"


def test_invalid_worker_configuration_does_not_create_database_or_start_server(
    tmp_path: Path, monkeypatch
):
    database = tmp_path / "approved-runs.sqlite3"
    monkeypatch.setenv("CELLERTZ_APPROVED_RUN_STORE", "sqlite")
    monkeypatch.setenv("CELLERTZ_APPROVED_RUN_DB", str(database))
    monkeypatch.setenv("CELLERTZ_APPROVED_RUN_WORKER_POLL_SECONDS", "0")

    def started(*_args, **_kwargs):
        raise AssertionError("server started")

    monkeypatch.setattr("cellertz.conversation.__main__.serve_conversation", started)
    with pytest.raises(ApprovedRunConfigurationError) as caught:
        main()
    assert caught.value.code == "invalid_approved_run_worker_poll_seconds"
    assert not database.exists()
    assert list(tmp_path.iterdir()) == []


def test_importing_the_store_does_not_create_a_database(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import importlib

    importlib.import_module("cellertz.application.approved_run_store")
    importlib.import_module("cellertz.application.approved_run_config")
    assert list(tmp_path.iterdir()) == []


def test_malformed_database_file_is_left_unchanged(tmp_path: Path):
    path = tmp_path / "approved-runs.sqlite3"
    path.write_bytes(b"not a database")
    before = path.read_bytes()
    with pytest.raises(ApprovedRunStoreError) as caught:
        SQLiteApprovedRunStore(path)
    assert caught.value.code == "approved_run_database_unreadable"
    assert path.read_bytes() == before


def test_public_reply_hides_driver_details(tmp_path: Path):
    service, _runner, store, _gate, _drafter, approval = _ready(tmp_path)
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    encoded = json.dumps(created.to_dict())
    for leak in ("sqlite", "SELECT", "Traceback", str(store._path), "OperationalError"):
        assert leak.casefold() not in encoded.casefold()
    status, body = handle_create_approved_run(
        service,
        json.dumps(
            {
                "session_id": SESSION,
                "approval_id": approval.approval_id,
                "idempotency_key": "run-1",
                "transition": "failed",
                "path": str(store._path),
            }
        ).encode(),
    )
    assert status == 400
    assert "transition" not in json.dumps(body)
    assert isinstance(created.run, ApprovedRun)
    store.close()


def _probe(path: Path) -> subprocess.CompletedProcess[str]:
    script = """
import sys
from pathlib import Path
from cellertz.application.approved_run import ApprovedRunStoreError
from cellertz.application.approved_run_store import SQLiteApprovedRunStore
try:
    opened = SQLiteApprovedRunStore(Path(sys.argv[1]))
except ApprovedRunStoreError as exc:
    sys.stdout.write(exc.code)
    raise SystemExit(0)
opened.close()
sys.stdout.write("opened")
raise SystemExit(0)
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


def test_second_process_cannot_fail_an_active_run(tmp_path: Path):
    service, _runner, store, _gate, _drafter, approval = _ready(tmp_path)
    queued = service._queued_new(
        approval,
        SESSION,
        service._gate.recorded_demonstration(
            session_id=SESSION, approval_id=approval.approval_id
        ).budget,
    )
    outcome, claimed = store.claim_create(
        scope=(SESSION, "active-key"),
        content_hash="active-hash",
        approval_key=(SESSION, approval.approval_id),
        run=queued,
    )
    assert outcome == "created"
    running = claimed.transition(
        "running", recorded_at=WHEN.isoformat(), text="Fixture runner started."
    )
    assert (
        store.commit_transition(running, expected_status="queued", expected_attempt=1)
        == "committed"
    )
    probe = _probe(store._path)
    assert probe.returncode == 0
    assert probe.stdout == "approved_run_store_busy"
    assert store.read_run(SESSION, claimed.run_id).status == "running"
    terminal = running.transition("failed", recorded_at=WHEN.isoformat(), text=_FAILURE)
    assert (
        store.commit_transition(terminal, expected_status="running", expected_attempt=1)
        == "committed"
    )
    assert store.read_run(SESSION, claimed.run_id).status == "failed"
    store.close()
    released = _probe(store._path)
    assert released.returncode == 0
    assert released.stdout == "opened"


def _claim_running(service, store, approval):
    queued = service._queued_new(
        approval,
        SESSION,
        service._gate.recorded_demonstration(
            session_id=SESSION, approval_id=approval.approval_id
        ).budget,
    )
    outcome, claimed = store.claim_create(
        scope=(SESSION, "active-key"),
        content_hash="active-hash",
        approval_key=(SESSION, approval.approval_id),
        run=queued,
    )
    assert outcome == "created"
    running = claimed.transition(
        "running", recorded_at=WHEN.isoformat(), text="Fixture runner started."
    )
    assert (
        store.commit_transition(running, expected_status="queued", expected_attempt=1)
        == "committed"
    )
    return claimed


def test_symlink_cannot_bypass_the_instance_lock(tmp_path: Path):
    service, _runner, store, _gate, _drafter, approval = _ready(tmp_path)
    claimed = _claim_running(service, store, approval)
    link = tmp_path / "alias.sqlite3"
    link.symlink_to(store._path)
    same_process = SQLiteApprovedRunStore(link)
    for candidate in (link, store._path):
        probe = _probe(candidate)
        assert probe.returncode == 0
        assert probe.stdout == "approved_run_store_busy"
        assert "Traceback" not in probe.stderr
    assert store.read_run(SESSION, claimed.run_id).status == "running"
    same_process.close()
    store.close()
    released = _probe(link)
    assert released.returncode == 0
    assert released.stdout == "opened"
    owner = SQLiteApprovedRunStore(link)
    real = _probe(store._path)
    assert real.returncode == 0
    assert real.stdout == "approved_run_store_busy"
    assert owner.read_run(SESSION, claimed.run_id).status == "running"
    owner.close()
    assert _probe(store._path).stdout == "opened"


def test_forked_child_cannot_bypass_the_instance_lock(tmp_path: Path):
    if not hasattr(os, "fork"):
        pytest.skip("fork is unavailable")
    service, _runner, store, _gate, _drafter, approval = _ready(tmp_path)
    claimed = _claim_running(service, store, approval)
    path = store._path
    store.close()
    script = """
import os
import sys
from pathlib import Path
from cellertz.application.approved_run import ApprovedRunStoreError
from cellertz.application.approved_run_store import SQLiteApprovedRunStore

database, session_id, run_id = sys.argv[1:]
store = SQLiteApprovedRunStore(Path(database))
current = store.read_run(session_id, run_id)
if current is None or current.status != "running":
    sys.stdout.write("not-running")
    store.close()
    raise SystemExit(0)
pid = os.fork()
if pid == 0:
    try:
        SQLiteApprovedRunStore(Path(database))
    except ApprovedRunStoreError as exc:
        os.write(1, exc.code.encode())
    else:
        os.write(1, b"opened")
    os._exit(0)
_child, status = os.waitpid(pid, 0)
again = store.read_run(session_id, run_id)
store.close()
if status != 0 or again is None or again.status != "running":
    sys.stdout.write("changed")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    probe = subprocess.run(
        [sys.executable, "-c", script, str(path), SESSION, claimed.run_id],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert probe.returncode == 0
    assert probe.stdout == "approved_run_store_busy"
    assert "Traceback" not in probe.stderr
    released = _probe(path)
    assert released.returncode == 0
    assert released.stdout == "opened"


def test_invalid_document_bytes_and_non_text_are_unusable(tmp_path: Path):
    service, runner, store, _gate, _drafter, approval = _ready(tmp_path)
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    assert created.run is not None
    calls = runner.calls
    for value in (b"\xff", 1):
        with pytest.raises(ApprovedRunStoreError) as caught:
            run_from_document(value)
        assert caught.value.code == "run_record_invalid"
    store._conn.execute("UPDATE runs SET document = x'ff' WHERE run_id = ?", (created.run.run_id,))
    observed = service.read(session_id=SESSION, run_id=created.run.run_id)
    assert observed.status == "run_record_invalid"
    assert observed.run is None
    assert observed.text == "The fixture run record could not be used."
    assert "Traceback" not in observed.text
    with pytest.raises(ApprovedRunStoreError) as caught:
        store.read_run(SESSION, created.run.run_id)
    assert caught.value.code == "run_record_invalid"
    store._conn.execute(
        "UPDATE runs SET document = ? WHERE run_id = ?",
        ("{}", created.run.run_id),
    )
    store._conn.text_factory = lambda _payload: 1
    observed = service.read(session_id=SESSION, run_id=created.run.run_id)
    assert observed.status == "run_record_invalid"
    assert observed.run is None
    assert runner.calls == calls
    store.close()


def test_concurrent_close_releases_the_process_lock_once(tmp_path: Path):
    path = tmp_path / "shared.sqlite3"
    owner = SQLiteApprovedRunStore(path)
    sibling = SQLiteApprovedRunStore(path)
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def close_owner() -> None:
        try:
            barrier.wait(timeout=5)
            owner.close()
        except BaseException as exc:  # noqa: BLE001 — the test records close races
            errors.append(exc)

    threads = [threading.Thread(target=close_owner) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    held = _probe(path)
    assert held.returncode == 0
    assert held.stdout == "approved_run_store_busy"
    assert "Traceback" not in held.stderr
    owner.close()
    still_held = _probe(path)
    assert still_held.returncode == 0
    assert still_held.stdout == "approved_run_store_busy"
    sibling.close()
    sibling.close()
    released = _probe(path)
    assert released.returncode == 0
    assert released.stdout == "opened"


def test_two_constructors_initialize_a_missing_database(tmp_path: Path, monkeypatch):
    path = tmp_path / "fresh.sqlite3"
    assert not path.exists()
    entered = threading.Barrier(2)
    original = SQLiteApprovedRunStore._connect

    def connect(self):
        original(self)
        entered.wait(timeout=5)

    monkeypatch.setattr(SQLiteApprovedRunStore, "_connect", connect)
    errors: list[BaseException] = []
    stores: list[SQLiteApprovedRunStore] = []

    def build() -> None:
        try:
            stores.append(SQLiteApprovedRunStore(path))
        except BaseException as exc:  # noqa: BLE001 — the test records constructor races
            errors.append(exc)

    threads = [threading.Thread(target=build) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert errors == []
    assert len(stores) == 2
    assert all(not thread.is_alive() for thread in threads)
    version = stores[0]._conn.execute("SELECT version FROM schema_meta WHERE id = 1").fetchone()[0]
    assert version == APPROVED_RUN_SCHEMA_VERSION
    for store in stores:
        store.close()


def test_reader_rejects_noncanonical_and_impossible_histories(tmp_path: Path):
    service, runner, store, _gate, _drafter, approval = _ready(tmp_path)
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    assert created.run is not None
    original = store._conn.execute(
        "SELECT document FROM runs WHERE run_id = ?",
        (created.run.run_id,),
    ).fetchone()["document"]
    calls = runner.calls

    def publish(document: dict, *, raw: str | None = None) -> None:
        payload = (
            raw
            if raw is not None
            else json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        )
        store._conn.execute(
            "UPDATE runs SET document = ?, status = ?, attempt = ? WHERE run_id = ?",
            (payload, document["status"], document["attempt"], created.run.run_id),
        )
        observed = service.read(session_id=SESSION, run_id=created.run.run_id)
        assert observed.status == "run_record_invalid"
        assert observed.run is None
        assert observed.text == "The fixture run record could not be used."
        assert runner.calls == calls

    incompatible = json.loads(original)
    incompatible["events"][-1]["text"] = _FAILURE
    publish(incompatible)

    jumped = json.loads(original)
    jumped["attempt"] = 3
    jumped["events"][-1]["attempt"] = 3
    publish(jumped)

    impossible = json.loads(original)
    impossible["events"] = [impossible["events"][0], impossible["events"][-1]]
    impossible["events"][1]["sequence"] = 2
    publish(impossible)

    divergent = json.loads(original)
    divergent["usage"]["session_id"] = "session_other"
    publish(divergent)

    pretty = json.dumps(json.loads(original), indent=2)
    publish(json.loads(original), raw=pretty)
    store.close()
