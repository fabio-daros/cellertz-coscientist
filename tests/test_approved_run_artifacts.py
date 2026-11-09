"""Phase 6H durable fixture results. Not scientific validation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import threading
from dataclasses import replace
from datetime import timedelta
from io import BytesIO
from pathlib import Path

import pytest

from cellertz.application.approved_run import (
    ApprovedArtifactRecord,
    ApprovedRunStoreError,
    FixtureApprovedRunRunner,
    OperationalArtifact,
    artifact_filename,
    require_fixture_classification,
    run_document,
    verify_artifact_bytes,
)
from cellertz.application.approved_run_store import (
    APPROVED_RUN_SCHEMA_VERSION,
    SQLiteApprovedRunStore,
)
from cellertz.application.approved_run_transport import (
    RUNS_PATH,
    ArtifactResponse,
    handle_list_approved_runs,
    handle_read_artifact,
)
from cellertz.application.conversation_transport import configured_handler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_approved_run import SESSION, WHEN, _finish, _service
from tests.test_approved_run_durability import _ready
from tests.test_human_approval import _decide, _stack

_NOTICES = (
    "fixture operational artifact",
    "not scientific validation",
    "no scientific action was taken",
    "no candidate protein was generated",
)


class _BadRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, *, run_id: str, attempt: int, request_digest: str):
        self.calls += 1
        payload = b"not a fixture package"
        return (
            OperationalArtifact(
                artifact_id=f"artifact_{run_id}_{attempt}_fixture_receipt",
                kind="fixture_receipt",
                content_sha256=hashlib.sha256(payload).hexdigest(),
                size_bytes=len(payload),
                media_type="application/json",
                content=payload,
            ),
        )


def _completed(tmp_path: Path):
    service, runner, store, gate, _drafter, approval = _ready(tmp_path)
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    finished = _finish(service, created)
    assert finished.run is not None and finished.run.status == "completed"
    return service, runner, store, gate, approval, finished


def _artifact_id(run_id: str, attempt: int = 1, kind: str = "fixture_receipt") -> str:
    return f"artifact_{run_id}_{attempt}_{kind}"


def test_fixture_package_is_deterministic_and_not_a_scientific_result(tmp_path: Path):
    _service_obj, runner, store, _gate, _approval, finished = _completed(tmp_path)
    run = finished.run
    assert run is not None
    again = FixtureApprovedRunRunner().run(
        run_id=run.run_id, attempt=run.attempt, request_digest=run.request_digest
    )
    assert [item.content_sha256 for item in again] == [
        item.content_sha256 for item in run.artifacts
    ]
    assert runner.calls == 1
    record = _service_obj.read_artifact(
        session_id=SESSION,
        run_id=run.run_id,
        artifact_id=_artifact_id(run.run_id),
    )
    assert isinstance(record, ApprovedArtifactRecord)
    text = record.content.decode("utf-8")
    for notice in _NOTICES:
        assert notice in text.casefold()
    encoded = text.casefold()
    for banned in ("pdb", "cif", "affinity", "bindcraft", "rfdiffusion", "mber"):
        assert banned not in encoded
    listed = _service_obj.list_runs(session_id=SESSION)
    assert listed.runs[0].run_id == run.run_id
    assert runner.calls == 1
    store.close()


def test_artifact_bytes_survive_reopen(tmp_path: Path):
    service, runner, store, gate, _approval, finished = _completed(tmp_path)
    run = finished.run
    assert run is not None
    before = service.read_artifact(
        session_id=SESSION,
        run_id=run.run_id,
        artifact_id=_artifact_id(run.run_id, 1, "operational_log"),
    )
    assert isinstance(before, ApprovedArtifactRecord)
    path = store._path
    store.close()
    reopened_service, _adapter = _service(gate, store=SQLiteApprovedRunStore(path))
    observed = reopened_service.read_artifact(
        session_id=SESSION,
        run_id=run.run_id,
        artifact_id=_artifact_id(run.run_id, 1, "operational_log"),
    )
    assert isinstance(observed, ApprovedArtifactRecord)
    assert observed.content == before.content
    assert runner.calls == 1
    reopened_service.close()


def test_history_is_session_scoped_newest_first_and_read_only(tmp_path: Path):
    service, runner, store, _gate, approval, finished = _completed(tmp_path)
    assert finished.run is not None
    base = service._queued_new(approval, SESSION, finished.run.budget)
    older = replace(
        base,
        run_id=f"run_{'a' * 32}",
        approval_id="approval_older",
        created_at=(WHEN - timedelta(seconds=5)).isoformat(),
        updated_at=(WHEN - timedelta(seconds=5)).isoformat(),
    )
    newer = replace(
        base,
        run_id=f"run_{'b' * 32}",
        approval_id="approval_newer",
        created_at=(WHEN + timedelta(seconds=5)).isoformat(),
        updated_at=(WHEN + timedelta(seconds=5)).isoformat(),
    )
    store.claim_create(
        scope=(SESSION, "older"),
        content_hash="older",
        approval_key=(SESSION, "approval_older"),
        run=older,
    )
    store.claim_create(
        scope=(SESSION, "newer"),
        content_hash="newer",
        approval_key=(SESSION, "approval_newer"),
        run=newer,
    )
    first = service.list_runs(session_id=SESSION)
    second = service.list_runs(session_id=SESSION)
    assert [run.run_id for run in first.runs] == [newer.run_id, finished.run.run_id, older.run_id]
    assert [run.to_dict() for run in second.runs] == [run.to_dict() for run in first.runs]
    assert service.list_runs(session_id="session_other").runs == ()
    assert runner.calls == 1
    encoded = json.dumps(first.to_dict())
    assert "not scientific validation" in encoded.casefold()
    for leak in ("sqlite", str(store._path), "Traceback", "TOKEN"):
        assert leak not in encoded
    store.close()


def test_wrong_session_and_foreign_artifact_are_denied(tmp_path: Path):
    service, runner, store, _gate, _approval, finished = _completed(tmp_path)
    run = finished.run
    assert run is not None
    other = service.read_artifact(
        session_id="session_other", run_id=run.run_id, artifact_id=_artifact_id(run.run_id)
    )
    assert not isinstance(other, ApprovedArtifactRecord)
    assert other.status == "run_absent"
    assert other.run is None
    foreign = service.read_artifact(
        session_id=SESSION,
        run_id=run.run_id,
        artifact_id=_artifact_id(f"run_{'c' * 32}"),
    )
    assert not isinstance(foreign, ApprovedArtifactRecord)
    assert foreign.status == "artifact_absent"
    missing = service.read_artifact(
        session_id=SESSION,
        run_id=f"run_{'d' * 32}",
        artifact_id=_artifact_id(f"run_{'d' * 32}"),
    )
    assert not isinstance(missing, ApprovedArtifactRecord)
    assert missing.status == "run_absent"
    assert runner.calls == 1
    store.close()


def test_retry_seals_its_snapshot_and_does_not_rewrite_it(tmp_path: Path):
    service, runner, store, _gate, _drafter, approval = _ready(tmp_path)
    failing = FixtureApprovedRunRunner(RuntimeError("Traceback TOKEN"))
    service._runner = failing
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    failed = _finish(service, created)
    assert failed.run is not None and failed.run.status == "failed"
    queued = service.retry(session_id=SESSION, run_id=failed.run.run_id, idempotency_key="retry-1")
    failed_again = _finish(service, queued)
    assert failed_again.run is not None and failed_again.run.attempt == 2
    snapshot = store._conn.execute(
        "SELECT snapshot FROM retry_keys WHERE idempotency_key = ?",
        ("retry-1",),
    ).fetchone()[0]
    service._runner = runner
    retried = service.retry(session_id=SESSION, run_id=failed.run.run_id, idempotency_key="retry-2")
    completed = _finish(service, retried)
    assert (
        completed.run is not None
        and completed.run.attempt == 3
        and completed.run.status == "completed"
    )
    assert (
        store._conn.execute(
            "SELECT snapshot FROM retry_keys WHERE idempotency_key = ?",
            ("retry-1",),
        ).fetchone()[0]
        == snapshot
    )
    assert _artifact_id(failed.run.run_id, 3, "fixture_manifest") not in snapshot
    current = service.read_artifact(
        session_id=SESSION,
        run_id=failed.run.run_id,
        artifact_id=_artifact_id(failed.run.run_id, 3, "fixture_manifest"),
    )
    hidden = service.read_artifact(
        session_id=SESSION,
        run_id=failed.run.run_id,
        artifact_id=_artifact_id(failed.run.run_id, 2, "fixture_manifest"),
    )
    assert isinstance(current, ApprovedArtifactRecord)
    assert not isinstance(hidden, ApprovedArtifactRecord)
    assert hidden.status == "artifact_absent"
    assert failing.calls == 2
    assert runner.calls == 1
    store.close()


def test_corrupt_artifact_bytes_fail_closed(tmp_path: Path):
    service, _runner, store, _gate, _approval, finished = _completed(tmp_path)
    run = finished.run
    assert run is not None
    artifact_id = _artifact_id(run.run_id, 1, "operational_log")
    stored = store._conn.execute(
        "SELECT content FROM artifact_bytes WHERE artifact_id = ?",
        (artifact_id,),
    ).fetchone()[0]
    flipped = bytes([stored[0] ^ 0x01]) + stored[1:]
    store._conn.execute(
        "UPDATE artifact_bytes SET content = ? WHERE artifact_id = ?",
        (flipped, artifact_id),
    )
    denied = service.read_artifact(session_id=SESSION, run_id=run.run_id, artifact_id=artifact_id)
    assert not isinstance(denied, ApprovedArtifactRecord)
    assert denied.status == "artifact_digest_mismatch"
    encoded = json.dumps(denied.to_dict())
    for leak in ("corrupt", "sqlite", "Traceback", str(store._path)):
        assert leak not in encoded
    store._conn.execute(
        "UPDATE artifact_bytes SET media_type = ? WHERE artifact_id = ?",
        ("application/octet-stream", _artifact_id(run.run_id)),
    )
    media = service.read_artifact(
        session_id=SESSION, run_id=run.run_id, artifact_id=_artifact_id(run.run_id)
    )
    assert not isinstance(media, ApprovedArtifactRecord)
    assert media.status == "artifact_media_type_rejected"
    store.close()


def test_artifact_verification_rejects_unsafe_bytes():
    artifact = OperationalArtifact(
        artifact_id="artifact_run_" + "a" * 32 + "_1_operational_log",
        kind="operational_log",
        content_sha256="ab" * 32,
        size_bytes=4,
        media_type="text/plain",
    )
    with pytest.raises(ApprovedRunStoreError) as size:
        verify_artifact_bytes(artifact, b"no")
    assert size.value.code == "artifact_size_mismatch"
    huge = OperationalArtifact(
        artifact_id=artifact.artifact_id,
        kind="operational_log",
        content_sha256="ab" * 32,
        size_bytes=65537,
        media_type="text/plain",
    )
    with pytest.raises(ApprovedRunStoreError) as oversized:
        verify_artifact_bytes(huge, b"x" * 65537)
    assert oversized.value.code == "artifact_too_large"
    binary = OperationalArtifact(
        artifact_id=artifact.artifact_id,
        kind="operational_log",
        content_sha256=hashlib.sha256(b"\xff").hexdigest(),
        size_bytes=1,
        media_type="text/plain",
    )
    with pytest.raises(ApprovedRunStoreError) as invalid:
        verify_artifact_bytes(binary, b"\xff")
    assert invalid.value.code == "artifact_invalid"


def test_bad_runner_output_does_not_persist_artifacts(tmp_path: Path):
    service, _runner, store, _gate, _drafter, approval = _ready(tmp_path)
    bad = _BadRunner()
    service._runner = bad
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    finished = _finish(service, created)
    assert finished.run is not None and finished.run.status == "failed"
    assert finished.run.artifacts == ()
    assert store._conn.execute("SELECT COUNT(*) FROM artifact_bytes").fetchone()[0] == 0
    assert bad.calls == 1
    store.close()


def test_artifact_insert_failure_rolls_back_the_terminal_transition(tmp_path: Path):
    service, runner, store, _gate, _drafter, approval = _ready(tmp_path)
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    assert created.run is not None
    store._fail_on = "artifact_bytes"
    finished = service.process_next()
    assert finished is not None and finished.status == "failed"
    assert store._conn.execute("SELECT COUNT(*) FROM artifact_bytes").fetchone()[0] == 0
    observed = service.read(session_id=SESSION, run_id=created.run.run_id)
    assert observed.run is not None and observed.run.status == "failed"
    assert "forced" not in json.dumps(observed.to_dict())
    assert runner.calls == 1
    store.close()


def test_readers_do_not_see_a_partial_artifact_set(tmp_path: Path, monkeypatch):
    service, _runner, store, _gate, _drafter, approval = _ready(tmp_path)
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    assert created.run is not None
    started = threading.Event()
    release = threading.Event()
    original = SQLiteApprovedRunStore._insert_artifact_bytes

    def blocking(self, run, contents):
        started.set()
        assert release.wait(5)
        return original(self, run, contents)

    monkeypatch.setattr(SQLiteApprovedRunStore, "_insert_artifact_bytes", blocking)
    worker = threading.Thread(target=service.process_next)
    worker.start()
    assert started.wait(2)
    path = store._path
    peek = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        assert peek.execute("SELECT status FROM runs").fetchone()[0] == "running"
        assert peek.execute("SELECT COUNT(*) FROM artifact_bytes").fetchone()[0] == 0
    finally:
        peek.close()
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    committed = sqlite3.connect(path)
    try:
        assert committed.execute("SELECT status FROM runs").fetchone()[0] == "completed"
        assert committed.execute("SELECT COUNT(*) FROM artifact_bytes").fetchone()[0] == 6
    finally:
        committed.close()
    store.close()


def test_close_waits_for_an_active_artifact_read(tmp_path: Path, monkeypatch):
    service, _runner, store, _gate, _approval, finished = _completed(tmp_path)
    run = finished.run
    assert run is not None
    started = threading.Event()
    release = threading.Event()
    original = SQLiteApprovedRunStore._read_artifact

    def slow(self, session_id, run_id, artifact_id):
        started.set()
        assert release.wait(5)
        return original(self, session_id, run_id, artifact_id)

    monkeypatch.setattr(SQLiteApprovedRunStore, "_read_artifact", slow)
    reader = threading.Thread(
        target=lambda: service.read_artifact(
            session_id=SESSION, run_id=run.run_id, artifact_id=_artifact_id(run.run_id)
        )
    )
    reader.start()
    assert started.wait(2)
    closed = threading.Event()

    def close_store() -> None:
        store.close()
        closed.set()

    closer = threading.Thread(target=close_store)
    closer.start()
    assert closed.wait(0.2) is False
    release.set()
    closer.join(5)
    reader.join(5)
    assert closed.is_set()
    assert not reader.is_alive()


def test_v1_database_migrates_and_newer_schema_is_rejected(tmp_path: Path):
    service, _runner, _store, _gate, _drafter, approval = _ready(tmp_path, name="source.sqlite3")
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    finished = _finish(service, created)
    assert finished.run is not None
    document = run_document(finished.run)
    service.close()
    path = tmp_path / "v1.sqlite3"
    _write_v1(
        path, finished.run.run_id, finished.run.session_id, finished.run.approval_id, document
    )
    before = path.read_bytes()
    migrated = SQLiteApprovedRunStore(path)
    assert migrated._conn.execute("SELECT version FROM schema_meta").fetchone()[0] == 2
    assert APPROVED_RUN_SCHEMA_VERSION == 2
    observed = migrated.read_run(SESSION, finished.run.run_id)
    assert observed is not None and observed.status == "completed"
    with pytest.raises(ApprovedRunStoreError) as missing:
        migrated.read_artifact(SESSION, finished.run.run_id, _artifact_id(finished.run.run_id))
    assert missing.value.code == "artifact_absent"
    migrated.close()
    assert path.read_bytes() != before
    reopened = SQLiteApprovedRunStore(path)
    assert reopened.read_run(SESSION, finished.run.run_id) is not None
    reopened.close()
    newer = tmp_path / "newer.sqlite3"
    _write_v1(
        newer, finished.run.run_id, finished.run.session_id, finished.run.approval_id, document
    )
    conn = sqlite3.connect(newer)
    conn.execute("UPDATE schema_meta SET version = 9")
    conn.commit()
    conn.close()
    untouched = newer.read_bytes()
    with pytest.raises(ApprovedRunStoreError) as rejected:
        SQLiteApprovedRunStore(newer)
    assert rejected.value.code == "unsupported_approved_run_schema"
    assert newer.read_bytes() == untouched


def test_transport_list_and_artifact_headers_are_safe(tmp_path: Path):
    service, runner, store, gate, _approval, finished = _completed(tmp_path)
    run = finished.run
    assert run is not None
    listed_status, listed = handle_list_approved_runs(service, SESSION, RUNS_PATH)
    assert listed_status == 200
    assert listed["runs"][0]["run_id"] == run.run_id
    assert listed["scientific_validation"] is False
    rejected, _payload = handle_list_approved_runs(
        service, SESSION, f"{RUNS_PATH}?session_id=session_other"
    )
    assert rejected == 400
    artifact_id = _artifact_id(run.run_id, 1, "fixture_summary")
    result = handle_read_artifact(service, SESSION, run.run_id, artifact_id)
    assert isinstance(result, ArtifactResponse)
    assert result.media_type == "text/markdown"
    assert result.filename == "fixture-fixture_summary.md"
    handler = configured_handler(object(), approvals=gate, runs=service, session_id=SESSION)
    status, headers, body = _invoke(handler, f"{RUNS_PATH}/{run.run_id}/artifacts/{artifact_id}")
    assert status == 200
    assert headers["Content-Type"] == "text/markdown"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cache-Control"] == "private, no-store"
    assert headers["Content-Disposition"] == 'attachment; filename="fixture-fixture_summary.md"'
    assert headers["Content-Length"] == str(len(body))
    assert "\r" not in headers["Content-Disposition"]
    assert "\n" not in headers["Content-Disposition"]
    assert b"no candidate protein was generated" in body.lower()
    error_status, error_headers, error_body = _invoke(
        handler, f"{RUNS_PATH}/{run.run_id}/artifacts/not-an-artifact"
    )
    assert error_status == 400
    assert error_headers["Content-Type"] == "application/json"
    assert "Content-Disposition" not in error_headers
    assert "X-Content-Type-Options" not in error_headers
    assert b"no candidate protein was generated" not in error_body.lower()
    malformed, _payload = handle_read_artifact(service, SESSION, "../secret", artifact_id)
    assert malformed == 400
    before = runner.calls
    handle_read_artifact(service, SESSION, run.run_id, artifact_id)
    assert runner.calls == before
    store.close()


def _write_v1(path: Path, run_id: str, session_id: str, approval_id: str, document: str) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE schema_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL
        );
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            approval_id TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            document TEXT NOT NULL,
            UNIQUE (session_id, approval_id)
        );
        CREATE TABLE create_keys (
            session_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            run_id TEXT NOT NULL,
            PRIMARY KEY (session_id, idempotency_key)
        );
        CREATE TABLE retry_keys (
            session_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            run_id TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            snapshot TEXT,
            PRIMARY KEY (session_id, idempotency_key)
        );
        """
    )
    conn.execute("INSERT INTO schema_meta (id, version) VALUES (1, 1)")
    conn.execute(
        """
        INSERT INTO runs (run_id, session_id, approval_id, status, attempt, document)
        VALUES (?, ?, ?, 'completed', 1, ?)
        """,
        (run_id, session_id, approval_id, document),
    )
    conn.commit()
    conn.close()


def _invoke(handler: type, path: str) -> tuple[int, dict[str, str], bytes]:
    instance = handler.__new__(handler)
    instance.path = path
    instance.headers = {}
    instance.rfile = BytesIO(b"")
    instance.wfile = BytesIO()
    instance.status_code = None
    headers: dict[str, str] = {}
    instance.send_response = lambda code: setattr(instance, "status_code", code)
    instance.send_header = lambda name, value: headers.__setitem__(name, value)
    instance.end_headers = lambda: None
    instance.do_GET()
    return instance.status_code, headers, instance.wfile.getvalue()


def test_other_session_stack_cannot_list_foreign_runs(tmp_path: Path):
    _service_obj, _runner, store, _gate, _approval, finished = _completed(tmp_path)
    assert finished.run is not None
    gate, _drafter, _memory, _dispatcher, reply = _stack(session_id="session_other", id_prefix="b")
    recorded = _decide(gate, reply)
    assert recorded.approval is not None
    other, _adapter = _service(gate, store=store, start=8)
    assert other.list_runs(session_id="session_other").runs == ()
    denied = other.read_artifact(
        session_id="session_other",
        run_id=finished.run.run_id,
        artifact_id=_artifact_id(finished.run.run_id),
    )
    assert not isinstance(denied, ApprovedArtifactRecord)
    assert denied.status == "run_absent"
    store.close()


def _canonical_text(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _artifact(content: bytes, *, media_type: str = "text/plain", kind: str = "operational_log"):
    return OperationalArtifact(
        artifact_id=f"artifact_run_{'a' * 32}_1_{kind}",
        kind=kind,
        content_sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        media_type=media_type,
        content=content,
    )


def test_integrity_checks_bytes_and_structure_not_english_sentences():
    prose = "\n".join(_NOTICES).encode()
    sentences = _artifact(prose)
    assert verify_artifact_bytes(sentences, prose) is None
    with pytest.raises(ApprovedRunStoreError) as prose_only:
        require_fixture_classification(sentences, prose, None)
    assert prose_only.value.code == "artifact_invalid"
    classified = _artifact(
        b"fixture: true\nscientific_validation: false\nscientific_candidate: false\nrewritten notice\n"
    )
    verify_artifact_bytes(classified, classified.content)
    require_fixture_classification(classified, classified.content, None)
    flags = {
        "fixture": True,
        "scientific_validation": False,
        "scientific_candidate": False,
        "classification": "operational fixture record",
    }
    encoded = _canonical_text(flags)
    receipt = _artifact(encoded, media_type="application/json", kind="fixture_receipt")
    parsed = verify_artifact_bytes(receipt, encoded)
    require_fixture_classification(receipt, encoded, parsed)
    assert b"not scientific validation" not in encoded
    duplicate = _artifact(
        b'{"fixture":true,"fixture":false}', media_type="application/json", kind="fixture_receipt"
    )
    with pytest.raises(ApprovedRunStoreError) as duplicated:
        verify_artifact_bytes(duplicate, duplicate.content)
    assert duplicated.value.code == "artifact_invalid"
    unordered = _artifact(
        b'{"scientific_candidate":false,"fixture":true,"scientific_validation":false}',
        media_type="application/json",
        kind="fixture_receipt",
    )
    with pytest.raises(ApprovedRunStoreError) as order:
        verify_artifact_bytes(unordered, unordered.content)
    assert order.value.code == "artifact_invalid"


def _checkpoint(path: Path) -> bytes:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.commit()
    conn.close()
    for suffix in ("-wal", "-shm"):
        leftover = Path(str(path) + suffix)
        if leftover.exists():
            leftover.unlink()
    return path.read_bytes()


def test_migration_failure_leaves_schema_version_1(tmp_path: Path):
    service, _runner, _store, _gate, _drafter, approval = _ready(tmp_path, name="source.sqlite3")
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    finished = _finish(service, created)
    assert finished.run is not None
    document = run_document(finished.run)
    service.close()
    path = tmp_path / "v1.sqlite3"
    _write_v1(
        path, finished.run.run_id, finished.run.session_id, finished.run.approval_id, document
    )
    conn = sqlite3.connect(path)
    conn.execute(
        """
        INSERT INTO runs (run_id, session_id, approval_id, status, attempt, document)
        VALUES (?, ?, 'approval_bad', 'completed', 1, '{')
        """,
        (f"run_{'e' * 32}", finished.run.session_id),
    )
    conn.commit()
    conn.close()
    with pytest.raises(ApprovedRunStoreError):
        SQLiteApprovedRunStore(path)
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT version FROM schema_meta").fetchone()[0] == 1
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    columns = [row[1] for row in conn.execute("PRAGMA table_info(runs)")]
    stored = conn.execute(
        "SELECT document FROM runs WHERE run_id = ?",
        (finished.run.run_id,),
    ).fetchone()[0]
    conn.close()
    assert "artifact_bytes" not in names
    assert "updated_at" not in columns
    assert stored == document


def test_migration_orders_equal_timestamps_and_reopen_does_not_write(tmp_path: Path):
    service, _runner, _store, _gate, _drafter, approval = _ready(tmp_path, name="source.sqlite3")
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    finished = _finish(service, created)
    assert finished.run is not None
    source = json.loads(run_document(finished.run))
    service.close()
    source["updated_at"] = "2026-10-08T12:00:00+00:00"
    first_id = f"run_{'a' * 32}"
    second_id = f"run_{'b' * 32}"
    first = json.dumps(
        {**source, "run_id": first_id, "approval_id": "approval_a"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    second = json.dumps(
        {**source, "run_id": second_id, "approval_id": "approval_b"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    path = tmp_path / "v1.sqlite3"
    _write_v1(path, first_id, SESSION, "approval_a", first)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        INSERT INTO runs (run_id, session_id, approval_id, status, attempt, document)
        VALUES (?, ?, 'approval_b', ?, ?, ?)
        """,
        (second_id, SESSION, source["status"], source["attempt"], second),
    )
    conn.commit()
    conn.close()
    migrated = SQLiteApprovedRunStore(path)
    rows = migrated._conn.execute(
        "SELECT run_id, document, updated_at FROM runs ORDER BY run_id"
    ).fetchall()
    assert [(row["run_id"], row["document"], row["updated_at"]) for row in rows] == [
        (first_id, first, source["updated_at"]),
        (second_id, second, source["updated_at"]),
    ]
    listed = migrated.list_runs(SESSION, limit=50)
    assert [run.run_id for run in listed] == [second_id, first_id]
    migrated.close()
    before = _checkpoint(path)
    reopened = SQLiteApprovedRunStore(path)
    assert reopened.read_run(SESSION, first_id) is not None
    reopened.close()
    assert _checkpoint(path) == before
    raced = tmp_path / "race.sqlite3"
    _write_v1(raced, first_id, SESSION, "approval_a", first)
    opened: list[SQLiteApprovedRunStore] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def open_once() -> None:
        try:
            barrier.wait(5)
            opened.append(SQLiteApprovedRunStore(raced))
        except BaseException as exc:  # noqa: BLE001 — the test records either racer's failure
            errors.append(exc)

    threads = [threading.Thread(target=open_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    try:
        assert errors == []
        assert len(opened) == 2
        assert opened[0]._conn.execute("SELECT version FROM schema_meta").fetchone()[0] == 2
        assert opened[0].read_run(SESSION, first_id) is not None
    finally:
        for store in opened:
            store.close()


def test_artifact_insert_failure_is_invisible_before_the_safe_failure(tmp_path, monkeypatch):
    service, _runner, store, _gate, _drafter, approval = _ready(tmp_path)
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    assert created.run is not None
    path = store._path
    seen: dict[str, tuple[str, int]] = {}
    original = SQLiteApprovedRunStore.commit_transition

    def wrapped(self, run, *, expected_status, expected_attempt):
        if run.status == "failed":
            conn = sqlite3.connect(path)
            status = conn.execute("SELECT status FROM runs").fetchone()[0]
            count = conn.execute("SELECT COUNT(*) FROM artifact_bytes").fetchone()[0]
            conn.close()
            seen["during"] = (status, count)
            self._fail_on = None
        else:
            self._fail_on = "artifact_bytes"
        return original(
            self, run, expected_status=expected_status, expected_attempt=expected_attempt
        )

    monkeypatch.setattr(SQLiteApprovedRunStore, "commit_transition", wrapped)
    finished = service.process_next()
    assert finished is not None and finished.status == "failed"
    assert seen["during"] == ("running", 0)
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT status FROM runs").fetchone()[0] == "failed"
    assert conn.execute("SELECT COUNT(*) FROM artifact_bytes").fetchone()[0] == 0
    conn.close()
    store.close()


def test_safe_failure_stops_after_two_writes(tmp_path, monkeypatch):
    service, _runner, store, _gate, _drafter, approval = _ready(tmp_path)
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    assert created.run is not None
    calls = {"n": 0}
    original = SQLiteApprovedRunStore.commit_transition

    def wrapped(self, run, *, expected_status, expected_attempt):
        calls["n"] += 1
        if run.status == "completed":
            self._fail_on = "artifact_bytes"
            return original(
                self, run, expected_status=expected_status, expected_attempt=expected_attempt
            )
        if calls["n"] == 2:
            raise ApprovedRunStoreError("approved_run_store_unavailable")
        self._fail_on = None
        return original(
            self, run, expected_status=expected_status, expected_attempt=expected_attempt
        )

    monkeypatch.setattr(SQLiteApprovedRunStore, "commit_transition", wrapped)
    finished = service.process_next()
    assert calls["n"] == 3
    assert finished is not None and finished.status == "failed"
    assert store.read_run(created.run.session_id, created.run.run_id).status == "failed"
    assert store._conn.execute("SELECT COUNT(*) FROM artifact_bytes").fetchone()[0] == 0
    store.close()


def test_committed_bytes_cannot_be_replaced(tmp_path: Path):
    service, _runner, store, _gate, _approval, finished = _completed(tmp_path)
    run = finished.run
    assert run is not None
    stored = store.read_run(SESSION, run.run_id)
    assert stored is not None
    artifact_id = stored.artifacts[0].artifact_id
    row = store._conn.execute(
        "SELECT content, content_sha256, media_type FROM artifact_bytes WHERE artifact_id = ?",
        (artifact_id,),
    ).fetchone()
    flipped = bytes([row["content"][0] ^ 1]) + row["content"][1:]
    replacements = (
        replace(
            stored.artifacts[0],
            content=flipped,
            content_sha256=hashlib.sha256(flipped).hexdigest(),
            size_bytes=len(flipped),
        ),
        replace(stored.artifacts[0], content=row["content"], content_sha256="ab" * 32),
        replace(stored.artifacts[0], content=row["content"], media_type="text/plain"),
    )
    for artifact in replacements:
        changed = replace(stored, artifacts=(artifact, *stored.artifacts[1:]))
        with pytest.raises(ApprovedRunStoreError) as caught:
            store.commit_transition(
                changed, expected_status="completed", expected_attempt=stored.attempt
            )
        assert caught.value.code == "artifact_immutable"
    assert (
        store.commit_transition(stored, expected_status="running", expected_attempt=stored.attempt)
        == "lost"
    )
    again = store._conn.execute(
        "SELECT content, content_sha256, media_type FROM artifact_bytes WHERE artifact_id = ?",
        (artifact_id,),
    ).fetchone()
    assert again["content"] == row["content"]
    assert again["content_sha256"] == row["content_sha256"]
    assert again["media_type"] == row["media_type"]
    service.close()


def test_duplicate_reused_and_non_terminal_artifacts_are_rejected(tmp_path: Path):
    service, runner, store, _gate, _drafter, approval = _ready(tmp_path)
    created = service.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="run-1"
    )
    assert created.run is not None
    running = store.claim_next_queued(recorded_at=WHEN.isoformat())
    assert running is not None
    package = list(
        FixtureApprovedRunRunner().run(
            run_id=running.run_id,
            attempt=running.attempt,
            request_digest=running.request_digest,
        )
    )
    duplicate = running.transition(
        "completed",
        recorded_at=WHEN.isoformat(),
        text="Fixture-only run completed. Not scientific validation.",
        artifacts=(*package, package[0]),
    )
    with pytest.raises(ApprovedRunStoreError) as duplicated:
        store.commit_transition(
            duplicate, expected_status="running", expected_attempt=running.attempt
        )
    assert duplicated.value.code == "artifact_immutable"
    attached = running.transition(
        "failed",
        recorded_at=WHEN.isoformat(),
        text="The fixture run failed. No scientific action was taken.",
        artifacts=tuple(package),
    )
    with pytest.raises(ApprovedRunStoreError) as failed:
        store.commit_transition(
            attached, expected_status="running", expected_attempt=running.attempt
        )
    assert failed.value.code == "artifact_invalid"
    dirty = replace(running, artifacts=tuple(package))
    with pytest.raises(ApprovedRunStoreError) as running_bytes:
        store.commit_transition(dirty, expected_status="running", expected_attempt=running.attempt)
    assert running_bytes.value.code == "artifact_invalid"
    foreign = tuple(
        replace(item, artifact_id=_artifact_id(f"run_{'c' * 32}", 1, item.kind)) for item in package
    )
    unrelated = running.transition(
        "completed",
        recorded_at=WHEN.isoformat(),
        text="Fixture-only run completed. Not scientific validation.",
        artifacts=foreign,
    )
    with pytest.raises(ApprovedRunStoreError) as other_run:
        store.commit_transition(
            unrelated, expected_status="running", expected_attempt=running.attempt
        )
    assert other_run.value.code == "artifact_invalid"
    assert store.read_run(SESSION, running.run_id).status == "running"
    assert store._conn.execute("SELECT COUNT(*) FROM artifact_bytes").fetchone()[0] == 0
    failed_run = running.transition(
        "failed",
        recorded_at=WHEN.isoformat(),
        text="The fixture run failed. No scientific action was taken.",
    )
    assert (
        store.commit_transition(
            failed_run, expected_status="running", expected_attempt=running.attempt
        )
        == "committed"
    )
    earlier = _EarlierAttemptRunner()
    service._runner = earlier
    queued = service.retry(session_id=SESSION, run_id=running.run_id, idempotency_key="retry-1")
    decoy = b'{"fixture":true}'
    decoy_id = _artifact_id(running.run_id, 1, "fixture_receipt")
    store._conn.execute(
        """
        INSERT INTO artifact_bytes (
            artifact_id, run_id, attempt, kind, media_type, size_bytes, content_sha256, content
        ) VALUES (?, ?, 1, 'fixture_receipt', 'application/json', ?, ?, ?)
        """,
        (decoy_id, running.run_id, len(decoy), hashlib.sha256(decoy).hexdigest(), decoy),
    )
    finished = _finish(service, queued)
    assert finished.run is not None and finished.run.status == "failed"
    kept = store._conn.execute(
        "SELECT content FROM artifact_bytes WHERE artifact_id = ?",
        (decoy_id,),
    ).fetchone()[0]
    assert kept == decoy
    hidden = service.read_artifact(session_id=SESSION, run_id=running.run_id, artifact_id=decoy_id)
    assert not isinstance(hidden, ApprovedArtifactRecord)
    assert hidden.status == "artifact_absent"
    assert earlier.calls == 1
    assert runner.calls == 0
    store.close()


class _EarlierAttemptRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, *, run_id: str, attempt: int, request_digest: str):
        self.calls += 1
        return FixtureApprovedRunRunner().run(
            run_id=run_id, attempt=attempt - 1, request_digest=request_digest
        )


def test_reads_stay_queued_and_cannot_combine_identifiers(tmp_path: Path):
    service, runner, _store, _gate, _approval, finished = _completed(tmp_path)
    run = finished.run
    assert run is not None
    calls = runner.calls
    listed = service.list_runs(session_id=SESSION)
    assert listed.runs[0].status == "completed"
    combined = service.read_artifact(
        session_id=SESSION,
        run_id=run.run_id,
        artifact_id=_artifact_id(f"run_{'d' * 32}", run.attempt, "fixture_receipt"),
    )
    older = service.read_artifact(
        session_id=SESSION,
        run_id=run.run_id,
        artifact_id=_artifact_id(run.run_id, run.attempt + 1, "fixture_receipt"),
    )
    assert not isinstance(combined, ApprovedArtifactRecord)
    assert not isinstance(older, ApprovedArtifactRecord)
    assert combined.status == "artifact_absent"
    assert older.status == "artifact_absent"
    assert runner.calls == calls
    service.close()
    fresh, fresh_runner, fresh_store, _gate, _drafter, approval = _ready(
        tmp_path, name="queued.sqlite3"
    )
    created = fresh.create(
        session_id=SESSION, approval_id=approval.approval_id, idempotency_key="queued"
    )
    assert created.run is not None and created.run.status == "queued"
    before = fresh_runner.calls
    assert fresh.list_runs(session_id=SESSION).runs[0].status == "queued"
    missing = fresh.read_artifact(
        session_id=SESSION,
        run_id=created.run.run_id,
        artifact_id=_artifact_id(created.run.run_id),
    )
    assert not isinstance(missing, ApprovedArtifactRecord)
    assert missing.status == "artifact_absent"
    assert fresh_runner.calls == before
    assert fresh_store.read_run(SESSION, created.run.run_id).status == "queued"
    fresh.close()


def test_filename_rejects_control_and_traversal_characters():
    artifact = OperationalArtifact(
        artifact_id=f"artifact_run_{'a' * 32}_1_fixture_summary",
        kind="fixture_summary",
        content_sha256="ab" * 32,
        size_bytes=1,
        media_type="text/markdown",
    )
    assert artifact_filename(artifact) == "fixture-fixture_summary.md"
    for kind in (
        "../fixture_summary",
        "fixture_summary\r\nX",
        "fixture\\summary",
        'fix"ture_summary',
        "fixture_summary\u0001",
    ):
        with pytest.raises(ApprovedRunStoreError) as caught:
            artifact_filename(replace(artifact, kind=kind))
        assert caught.value.code == "artifact_media_type_rejected"


def _assert_closed(exc: ApprovedRunStoreError, store: SQLiteApprovedRunStore, leaked: str) -> None:
    assert exc.code in {"artifact_invalid", "artifact_too_large", "run_record_invalid"}
    rendered = str(exc)
    assert leaked not in rendered
    for secret in ("sqlite", "Traceback", str(store._path)):
        assert secret.casefold() not in rendered.casefold()


def test_corrupt_artifact_storage_classes_fail_closed(tmp_path: Path, monkeypatch):
    service, _runner, store, _gate, _approval, finished = _completed(tmp_path)
    run = finished.run
    assert run is not None
    artifact_id = _artifact_id(run.run_id, 1, "fixture_receipt")
    original = store._conn.execute(
        "SELECT content, size_bytes, content_sha256, media_type, kind, attempt FROM artifact_bytes WHERE artifact_id = ?",
        (artifact_id,),
    ).fetchone()

    def restore() -> None:
        store._conn.execute(
            """
            UPDATE artifact_bytes
            SET content = ?, size_bytes = ?, content_sha256 = ?, media_type = ?, kind = ?, attempt = ?
            WHERE artifact_id = ?
            """,
            (
                original["content"],
                original["size_bytes"],
                original["content_sha256"],
                original["media_type"],
                original["kind"],
                original["attempt"],
                artifact_id,
            ),
        )

    def deny(code: str) -> None:
        with pytest.raises(ApprovedRunStoreError) as caught:
            store.read_artifact(SESSION, run.run_id, artifact_id)
        assert caught.value.code == code
        _assert_closed(caught.value, store, "forced")
        reply = service.read_artifact(
            session_id=SESSION, run_id=run.run_id, artifact_id=artifact_id
        )
        assert not isinstance(reply, ApprovedArtifactRecord)
        encoded = json.dumps(reply.to_dict())
        for secret in ("sqlite", "Traceback", str(store._path), "MemoryError"):
            assert secret not in encoded

    store._conn.execute(
        "UPDATE artifact_bytes SET content = ? WHERE artifact_id = ?",
        (7, artifact_id),
    )
    deny("artifact_invalid")
    restore()
    real_bytes = bytes

    def refuse_integer(value: object = b"", *args: object, **kwargs: object) -> bytes:
        if type(value) is int:
            raise AssertionError("integer storage was copied into a buffer")
        return real_bytes(value, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("builtins.bytes", refuse_integer)
    store._conn.execute(
        "UPDATE artifact_bytes SET content = ? WHERE artifact_id = ?",
        (2**62, artifact_id),
    )
    deny("artifact_invalid")
    monkeypatch.undo()
    restore()
    store._conn.execute(
        "UPDATE artifact_bytes SET content = ? WHERE artifact_id = ?",
        ("not-a-blob", artifact_id),
    )
    deny("artifact_invalid")
    restore()
    store._conn.execute("PRAGMA foreign_keys = OFF")
    store._conn.execute("ALTER TABLE artifact_bytes RENAME TO artifact_bytes_kept")
    store._conn.execute(
        """
        CREATE TABLE artifact_bytes (
            artifact_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            kind TEXT NOT NULL,
            media_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            content
        )
        """
    )
    store._conn.execute(
        """
        INSERT INTO artifact_bytes
        SELECT artifact_id, run_id, attempt, kind, media_type, size_bytes, content_sha256, content
        FROM artifact_bytes_kept
        """
    )
    store._conn.execute("DROP TABLE artifact_bytes_kept")
    store._conn.execute(
        "UPDATE artifact_bytes SET content = NULL WHERE artifact_id = ?",
        (artifact_id,),
    )
    deny("artifact_invalid")
    store._conn.execute(
        "UPDATE artifact_bytes SET content = ?, size_bytes = ? WHERE artifact_id = ?",
        (b"x" * 65537, 65537, artifact_id),
    )
    deny("artifact_too_large")
    store._conn.execute(
        """
        UPDATE artifact_bytes
        SET content = ?, size_bytes = ?, attempt = ?, media_type = ?, kind = ?, content_sha256 = ?
        WHERE artifact_id = ?
        """,
        (original["content"], -1, True, 12, 1.5, "NOT-A-DIGEST", artifact_id),
    )
    deny("artifact_invalid")
    service.close()


def _preserve_updated_at(conn: sqlite3.Connection, run_id: str, value: object) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("ALTER TABLE runs RENAME TO runs_kept")
    conn.execute(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            approval_id TEXT NOT NULL,
            status TEXT NOT NULL,
            attempt INTEGER NOT NULL,
            updated_at,
            document TEXT NOT NULL,
            UNIQUE (session_id, approval_id)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO runs (run_id, session_id, approval_id, status, attempt, updated_at, document)
        SELECT run_id, session_id, approval_id, status, attempt, updated_at, document
        FROM runs_kept
        """
    )
    conn.execute("DROP TABLE runs_kept")
    conn.execute("UPDATE runs SET updated_at = ? WHERE run_id = ?", (value, run_id))


def test_denormalized_updated_at_must_match_the_document(tmp_path: Path):
    _service_obj, _runner, store, _gate, _approval, finished = _completed(tmp_path)
    run = finished.run
    assert run is not None
    document_time = run.updated_at

    def deny() -> None:
        with pytest.raises(ApprovedRunStoreError) as listed:
            store.list_runs(SESSION, limit=50)
        assert listed.value.code == "run_record_invalid"
        with pytest.raises(ApprovedRunStoreError) as read:
            store.read_run(SESSION, run.run_id)
        assert read.value.code == "run_record_invalid"
        for caught in (listed.value, read.value):
            rendered = str(caught)
            assert "sqlite" not in rendered.casefold()
            assert "Traceback" not in rendered
            assert str(store._path) not in rendered

    store._conn.execute(
        "UPDATE runs SET updated_at = ? WHERE run_id = ?",
        ("2020-01-01T00:00:00+00:00", run.run_id),
    )
    deny()
    store._conn.execute(
        "UPDATE runs SET updated_at = ? WHERE run_id = ?",
        ("not-a-timestamp", run.run_id),
    )
    deny()
    _preserve_updated_at(store._conn, run.run_id, 7)
    observed = store._conn.execute(
        "SELECT typeof(updated_at), updated_at FROM runs WHERE run_id = ?",
        (run.run_id,),
    ).fetchone()
    assert observed[0] == "integer"
    assert observed[1] == 7
    deny()
    _preserve_updated_at(store._conn, run.run_id, None)
    missing = store._conn.execute(
        "SELECT updated_at FROM runs WHERE run_id = ?",
        (run.run_id,),
    ).fetchone()
    assert missing[0] is None
    deny()
    store._conn.execute(
        "UPDATE runs SET updated_at = ? WHERE run_id = ?",
        (document_time, run.run_id),
    )
    assert store.read_run(SESSION, run.run_id) is not None
    store.close()
