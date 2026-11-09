"""Durable operational store for scientific runs.

The database is application state. It is not scientific memory.
Importing this module does not create a database.
"""

from __future__ import annotations

import errno
import fcntl
import os
import sqlite3
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.pdl1_attempt import ExternalAttempt, reservation_unchanged, retry_flag
from cellertz.application.scientific_run import (
    RETRY_QUEUED_TEXT,
    RUNNING_TEXT,
    ScientificArtifact,
    ScientificRun,
    ScientificRunStoreError,
    _contents_match,
    artifact_filename,
    freeze_run,
    interruption_text,
    run_document,
    run_from_document,
)

SCHEMA_VERSION = 1
_REQUIRED_TABLES = frozenset({"schema_meta", "runs", "create_keys", "retry_keys", "artifacts"})
_instance_guard = threading.Lock()
_instance_owners: dict[str, list[int]] = {}
_init_guard = threading.Lock()
_init_locks: dict[str, threading.Lock] = {}


class SQLiteScientificRunStore:
    """One process owns the resolved database. A symlink is the same file."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path).resolve()
        self._lock = threading.RLock()
        self._fail_on: str | None = None
        self._conn: sqlite3.Connection | None = None
        self._instance_held = False
        _acquire_instance_lock(self._path)
        self._instance_held = True
        try:
            self._open()
        except BaseException:
            self.close()
            raise

    def claim_create(
        self, *, scope: tuple[str, str], content_hash: str, run: ScientificRun
    ) -> tuple[str, ScientificRun]:
        try:
            return self._claim_create_once(scope=scope, content_hash=content_hash, run=run)
        except sqlite3.IntegrityError:
            try:
                return self._claim_create_once(scope=scope, content_hash=content_hash, run=run)
            except sqlite3.IntegrityError:
                raise ScientificRunStoreError("scientific_run_store_unavailable") from None
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def confirm(self, run_id: str) -> ScientificRun | None:
        try:
            with self._transaction():
                self._failpoint("confirm")
                current = self._load_any(run_id)
                if current is None:
                    return None
                if current.confirmed:
                    return current
                self._conn.execute(
                    "UPDATE runs SET confirmed = 1 WHERE run_id = ? AND confirmed = 0",
                    (run_id,),
                )
                if self._conn.execute("SELECT changes()").fetchone()[0] != 1:
                    raise ScientificRunStoreError("scientific_run_record_invalid")
                return self._require(self._load_any(run_id))
        except ScientificRunStoreError:
            raise
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def delete_provisional(self, run_id: str) -> bool:
        try:
            with self._transaction():
                row = self._conn.execute(
                    "SELECT confirmed FROM runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if row is None or row["confirmed"] != 0:
                    return False
                self._conn.execute("DELETE FROM create_keys WHERE run_id = ?", (run_id,))
                self._conn.execute(
                    "DELETE FROM runs WHERE run_id = ? AND confirmed = 0",
                    (run_id,),
                )
                return self._conn.execute("SELECT changes()").fetchone()[0] == 1
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def unconfirmed(self) -> tuple[ScientificRun, ...]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT run_id FROM runs WHERE confirmed = 0 ORDER BY run_id"
                ).fetchall()
                return tuple(self._require(self._load_any(row["run_id"])) for row in rows)
        except ScientificRunStoreError:
            raise
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def claim_retry(
        self,
        *,
        run_id: str,
        session_id: str,
        scope: tuple[str, str],
        content_hash: str,
        expected_attempt: int,
        recorded_at: str,
    ) -> tuple[str, ScientificRun | None]:
        try:
            return self._claim_retry_once(
                run_id=run_id,
                session_id=session_id,
                scope=scope,
                content_hash=content_hash,
                expected_attempt=expected_attempt,
                recorded_at=recorded_at,
            )
        except sqlite3.IntegrityError:
            try:
                return self._claim_retry_once(
                    run_id=run_id,
                    session_id=session_id,
                    scope=scope,
                    content_hash=content_hash,
                    expected_attempt=expected_attempt,
                    recorded_at=recorded_at,
                )
            except sqlite3.IntegrityError:
                raise ScientificRunStoreError("scientific_run_store_unavailable") from None
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def claim_next_queued(self, *, recorded_at: str) -> ScientificRun | None:
        try:
            with self._transaction():
                row = self._conn.execute(
                    """
                    SELECT run_id FROM runs
                    WHERE status = 'queued' AND confirmed = 1
                    ORDER BY created_at, run_id
                    LIMIT 1
                    """
                ).fetchone()
                if row is None:
                    return None
                queued = self._require(self._load_any(row["run_id"]))
                running = freeze_run(
                    queued.transition("running", recorded_at=recorded_at, text=RUNNING_TEXT)
                )
                if not self._update_run(running, "queued", queued.attempt):
                    return None
                return running
        except ScientificRunStoreError:
            raise
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def commit_transition(
        self,
        run: ScientificRun,
        *,
        expected_status: str,
        expected_attempt: int,
        contents: Mapping[str, bytes],
    ) -> str:
        try:
            with self._transaction():
                current = self._load_any(run.run_id)
                if (
                    current is None
                    or current.status != expected_status
                    or current.attempt != expected_attempt
                    or not current.confirmed
                ):
                    return "lost"
                stored = freeze_run(replace_confirmed(run))
                if not _contents_match(stored, contents):
                    raise ScientificRunStoreError("scientific_run_record_invalid")
                if not self._update_run(stored, expected_status, expected_attempt):
                    return "lost"
                if stored.status == "completed":
                    self._insert_artifacts(stored, contents)
                self._seal(stored)
                return "committed"
        except ScientificRunStoreError:
            raise
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def read_run(self, session_id: str, run_id: str) -> ScientificRun | None:
        try:
            with self._lock:
                current = self._load_any(run_id)
                if current is None or current.session_id != session_id or not current.confirmed:
                    return None
                return current
        except ScientificRunStoreError:
            raise
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def read_by_authorization(self, session_id: str, authorization_id: str) -> ScientificRun | None:
        try:
            with self._lock:
                row = self._conn.execute(
                    """
                    SELECT run_id FROM runs
                    WHERE session_id = ? AND authorization_id = ?
                    """,
                    (session_id, authorization_id),
                ).fetchone()
                if row is None:
                    return None
                return self._load_any(row["run_id"])
        except ScientificRunStoreError:
            raise
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def list_runs(self, session_id: str, *, limit: int) -> tuple[ScientificRun, ...]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT run_id FROM runs
                    WHERE session_id = ? AND confirmed = 1
                    ORDER BY updated_at DESC, run_id DESC
                    LIMIT ?
                    """,
                    (session_id, limit),
                ).fetchall()
                return tuple(self._require(self._load_any(row["run_id"])) for row in rows)
        except ScientificRunStoreError:
            raise
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def read_artifact(
        self, session_id: str, run_id: str, artifact_id: str
    ) -> tuple[ScientificArtifact, bytes]:
        try:
            with self._lock:
                current = self._load_any(run_id)
                if current is None or current.session_id != session_id or not current.confirmed:
                    raise ScientificRunStoreError("run_absent")
                artifact = next(
                    (item for item in current.artifacts if item.artifact_id == artifact_id),
                    None,
                )
                row = self._conn.execute(
                    """
                    SELECT typeof(content) AS content_type,
                           length(content) AS content_length,
                           typeof(attempt) AS attempt_type,
                           attempt,
                           typeof(size) AS size_type,
                           size,
                           typeof(sha256) AS digest_type,
                           sha256,
                           typeof(kind) AS kind_type,
                           kind,
                           typeof(media_type) AS media_type_type,
                           media_type
                    FROM artifacts
                    WHERE artifact_id = ? AND run_id = ? AND session_id = ?
                    """,
                    (artifact_id, run_id, session_id),
                ).fetchone()
                if artifact is None:
                    code = "artifact_attempt_unavailable" if row is not None else "artifact_absent"
                    raise ScientificRunStoreError(code)
                if row is None:
                    raise ScientificRunStoreError("artifact_absent")
                _artifact_metadata(row, artifact)
                stored = self._conn.execute(
                    """
                    SELECT content FROM artifacts
                    WHERE artifact_id = ? AND run_id = ? AND session_id = ?
                    """,
                    (artifact_id, run_id, session_id),
                ).fetchone()
                content = None if stored is None else stored["content"]
                if type(content) is not bytes or len(content) != artifact.size:
                    raise ScientificRunStoreError("artifact_invalid")
                from cellertz.application.scientific_run import artifact_bytes_match

                if not artifact_bytes_match(current, artifact, content):
                    raise ScientificRunStoreError("artifact_invalid")
                artifact_filename(artifact)
                return artifact, content
        except ScientificRunStoreError:
            raise
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def recover_interrupted(self, *, recorded_at: str) -> tuple[ScientificRun, ...]:
        try:
            with self._transaction():
                rows = self._conn.execute(
                    "SELECT run_id FROM runs WHERE status = 'running' AND confirmed = 1"
                ).fetchall()
                recovered: list[ScientificRun] = []
                for row in rows:
                    current = self._require(self._load_any(row["run_id"]))
                    updated = freeze_run(
                        current.transition(
                            "failed",
                            recorded_at=recorded_at,
                            text=interruption_text(
                                self._external_state(current.run_id, current.attempt)
                            ),
                        )
                    )
                    if not self._update_run(updated, "running", current.attempt):
                        raise ScientificRunStoreError("scientific_run_record_invalid")
                    self._seal(updated)
                    recovered.append(updated)
                return tuple(recovered)
        except ScientificRunStoreError:
            raise
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def reserve_external(self, attempt: object) -> None:
        self._write_external(attempt, insert=True)

    def save_external(self, attempt: object) -> None:
        self._write_external(attempt, insert=False)

    def list_external(self) -> tuple[object, ...]:
        try:
            with self._lock:
                rows = self._conn.execute("SELECT * FROM external_attempts").fetchall()
                return tuple(_attempt_from_row(row) for row in rows)
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def read_run_any(self, run_id: str) -> ScientificRun | None:
        try:
            with self._lock:
                return self._load_any(run_id)
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def external_state(self, run_id: str, attempt: int) -> str | None:
        try:
            with self._lock:
                return self._external_state(run_id, attempt)
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            if self._instance_held:
                _release_instance_lock(self._path)
                self._instance_held = False

    def _open(self) -> None:
        if self._path.exists():
            version, tables = _peek(self._path)
            if version is not None and version > SCHEMA_VERSION:
                raise ScientificRunStoreError("unsupported_scientific_run_schema")
            if version == SCHEMA_VERSION and _REQUIRED_TABLES <= tables:
                self._connect()
                self._ensure_attempt_table()
                return
            if version is None and not tables:
                self._connect()
                self._ensure_schema()
                return
            raise ScientificRunStoreError("scientific_run_schema_inconsistent")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connect()
        self._ensure_schema()

    def _claim_create_once(
        self, *, scope: tuple[str, str], content_hash: str, run: ScientificRun
    ) -> tuple[str, ScientificRun]:
        with self._transaction():
            self._failpoint("create_run")
            found = self._lookup_create(scope, content_hash)
            if found is not None:
                return found
            existing = self._conn.execute(
                """
                SELECT run_id FROM runs
                WHERE session_id = ? AND authorization_id = ?
                """,
                (run.session_id, run.authorization_id),
            ).fetchone()
            if existing is not None:
                self._insert_create_key(scope, content_hash, existing["run_id"])
                self._failpoint("create_key")
                return "replay", self._require(self._load_any(existing["run_id"]))
            if run.confirmed or run.status != "queued" or run.attempt != 1:
                raise ScientificRunStoreError("scientific_run_record_invalid")
            stored = freeze_run(run)
            self._insert_run(stored)
            self._insert_create_key(scope, content_hash, stored.run_id)
            self._failpoint("create_key")
            return "created", stored

    def _claim_retry_once(
        self,
        *,
        run_id: str,
        session_id: str,
        scope: tuple[str, str],
        content_hash: str,
        expected_attempt: int,
        recorded_at: str,
    ) -> tuple[str, ScientificRun | None]:
        with self._transaction():
            found = self._lookup_retry(scope, content_hash, run_id)
            if found is not None:
                return found
            current = self._load_any(run_id)
            if current is None or current.session_id != session_id or not current.confirmed:
                return "absent", None
            if current.status != "failed" or current.attempt != expected_attempt:
                return "retry_not_allowed", current
            if self._retry_blocked(run_id):
                return "retry_not_allowed", current
            queued = freeze_run(
                current.transition(
                    "queued",
                    recorded_at=recorded_at,
                    text=RETRY_QUEUED_TEXT,
                    attempt=current.attempt + 1,
                )
            )
            if not self._update_run(queued, "failed", expected_attempt):
                return "retry_not_allowed", self._load_any(run_id)
            self._failpoint("retry_key")
            self._conn.execute(
                """
                INSERT INTO retry_keys (
                    session_id, idempotency_key, content_hash, run_id, attempt, snapshot
                ) VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (scope[0], scope[1], content_hash, run_id, queued.attempt),
            )
            return "retried", queued

    def _lookup_create(
        self, scope: tuple[str, str], content_hash: str
    ) -> tuple[str, ScientificRun] | None:
        row = self._conn.execute(
            """
            SELECT content_hash, run_id FROM create_keys
            WHERE session_id = ? AND idempotency_key = ?
            """,
            scope,
        ).fetchone()
        if row is None:
            return None
        current = self._require(self._load_any(row["run_id"]))
        if row["content_hash"] != content_hash:
            return "conflict", current
        return "replay", current

    def _lookup_retry(
        self, scope: tuple[str, str], content_hash: str, run_id: str
    ) -> tuple[str, ScientificRun | None] | None:
        row = self._conn.execute(
            """
            SELECT typeof(content_hash) AS content_hash_type, content_hash,
                   typeof(run_id) AS run_id_type, run_id,
                   typeof(attempt) AS attempt_type, attempt,
                   typeof(snapshot) AS snapshot_type, snapshot,
                   typeof(session_id) AS session_type, session_id
            FROM retry_keys
            WHERE session_id = ? AND idempotency_key = ?
            """,
            scope,
        ).fetchone()
        if row is None:
            return None
        if not _retry_types(row) or row["session_id"] != scope[0]:
            raise ScientificRunStoreError("scientific_run_record_invalid")
        current = self._load_any(row["run_id"])
        snapshot = _snapshot(row["snapshot"], row)
        if row["content_hash"] != content_hash or row["run_id"] != run_id:
            return "conflict", snapshot if snapshot is not None else current
        if snapshot is not None:
            return "replay", snapshot
        if current is None or current.attempt != row["attempt"]:
            raise ScientificRunStoreError("scientific_run_record_invalid")
        return "replay", current

    def _insert_run(self, run: ScientificRun) -> None:
        self._conn.execute(
            """
            INSERT INTO runs (
                run_id, session_id, authorization_id, binding_hash, status, attempt,
                confirmed, event_count, document, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_id,
                run.session_id,
                run.authorization_id,
                run.binding_hash,
                run.status,
                run.attempt,
                1 if run.confirmed else 0,
                len(run.events),
                run_document(run),
                run.created_at,
                run.updated_at,
            ),
        )

    def _insert_create_key(self, scope: tuple[str, str], content_hash: str, run_id: str) -> None:
        self._conn.execute(
            """
            INSERT INTO create_keys (session_id, idempotency_key, content_hash, run_id)
            VALUES (?, ?, ?, ?)
            """,
            (scope[0], scope[1], content_hash, run_id),
        )

    def _update_run(self, run: ScientificRun, expected_status: str, expected_attempt: int) -> bool:
        previous = self._require(self._load_any(run.run_id))
        if run.events[: len(previous.events)] != previous.events or len(run.events) <= len(
            previous.events
        ):
            raise ScientificRunStoreError("scientific_run_record_invalid")
        self._conn.execute(
            """
            UPDATE runs
            SET status = ?, attempt = ?, event_count = ?, document = ?, updated_at = ?,
                binding_hash = ?
            WHERE run_id = ? AND session_id = ? AND status = ? AND attempt = ?
              AND event_count = ? AND confirmed = 1
            """,
            (
                run.status,
                run.attempt,
                len(run.events),
                run_document(run),
                run.updated_at,
                run.binding_hash,
                run.run_id,
                run.session_id,
                expected_status,
                expected_attempt,
                len(previous.events),
            ),
        )
        return self._conn.execute("SELECT changes()").fetchone()[0] == 1

    def _insert_artifacts(self, run: ScientificRun, contents: Mapping[str, bytes]) -> None:
        for artifact in run.artifacts:
            self._conn.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, run_id, session_id, attempt, kind, media_type,
                    sha256, size, content
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.artifact_id,
                    artifact.run_id,
                    artifact.session_id,
                    artifact.attempt,
                    artifact.kind,
                    artifact.media_type,
                    artifact.sha256,
                    artifact.size,
                    contents[artifact.artifact_id],
                ),
            )

    def _seal(self, run: ScientificRun) -> None:
        if run.status not in {"completed", "failed"}:
            return
        self._conn.execute(
            """
            UPDATE retry_keys SET snapshot = ?
            WHERE run_id = ? AND attempt = ? AND snapshot IS NULL
            """,
            (run_document(run), run.run_id, run.attempt),
        )

    def _load_any(self, run_id: str) -> ScientificRun | None:
        row = self._conn.execute(
            """
            SELECT run_id, session_id, authorization_id, binding_hash, status, attempt,
                   confirmed, event_count, document, created_at, updated_at
            FROM runs WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        kinds = self._conn.execute(
            """
            SELECT typeof(run_id), typeof(session_id), typeof(authorization_id),
                   typeof(binding_hash), typeof(status), typeof(attempt), typeof(confirmed),
                   typeof(event_count), typeof(document), typeof(created_at), typeof(updated_at)
            FROM runs WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if kinds is None or any(kind != "text" for kind in kinds[:5]) or kinds[5] != "integer":
            raise ScientificRunStoreError("scientific_run_record_invalid")
        if (
            kinds[6] != "integer"
            or kinds[7] != "integer"
            or any(kind != "text" for kind in kinds[8:])
        ):
            raise ScientificRunStoreError("scientific_run_record_invalid")
        if row["confirmed"] not in {0, 1} or type(row["document"]) is not str:
            raise ScientificRunStoreError("scientific_run_record_invalid")
        try:
            run = run_from_document(row["document"], confirmed=bool(row["confirmed"]))
        except ScientificRunStoreError:
            raise
        except (ApplicationContractError, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_record_invalid") from None
        if (
            run.run_id != row["run_id"]
            or run.session_id != row["session_id"]
            or run.authorization_id != row["authorization_id"]
            or run.binding_hash != row["binding_hash"]
            or run.status != row["status"]
            or run.attempt != row["attempt"]
            or run.created_at != row["created_at"]
            or run.updated_at != row["updated_at"]
            or len(run.events) != row["event_count"]
            or run.confirmed is not bool(row["confirmed"])
        ):
            raise ScientificRunStoreError("scientific_run_record_invalid")
        return run

    def _require(self, run: ScientificRun | None) -> ScientificRun:
        if run is None:
            raise ScientificRunStoreError("scientific_run_record_invalid")
        return run

    def _connect(self) -> None:
        try:
            with _database_init_lock(self._path):
                self._conn = sqlite3.connect(
                    self._path, isolation_level=None, check_same_thread=False
                )
                self._conn.row_factory = sqlite3.Row
                self._conn.execute("PRAGMA foreign_keys = ON")
                self._conn.execute("PRAGMA busy_timeout = 5000")
                mode = self._conn.execute("PRAGMA journal_mode").fetchone()
                if mode is None or str(mode[0]).lower() != "wal":
                    self._conn.execute("PRAGMA journal_mode = WAL")
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def _ensure_schema(self) -> None:
        try:
            with self._transaction():
                version, tables = _catalog(self._conn)
                if version == SCHEMA_VERSION and _REQUIRED_TABLES <= tables:
                    return
                if version is not None and version > SCHEMA_VERSION:
                    raise ScientificRunStoreError("unsupported_scientific_run_schema")
                if version is None and not tables:
                    self._create_tables()
                    return
                raise ScientificRunStoreError("scientific_run_schema_inconsistent")
        except ScientificRunStoreError:
            raise
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def _ensure_attempt_table(self) -> None:
        try:
            self._create_attempt_table()
        except sqlite3.Error:
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def _create_attempt_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS external_attempts (
                run_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                authorization_id TEXT NOT NULL,
                binding_hash TEXT NOT NULL,
                deployment_id TEXT NOT NULL,
                daemon_id TEXT NOT NULL,
                container_name TEXT NOT NULL,
                container_id TEXT,
                state TEXT NOT NULL,
                retry_allowed INTEGER NOT NULL,
                output_sha256 TEXT,
                PRIMARY KEY (run_id, attempt)
            )
            """
        )

    def _write_external(self, attempt: object, *, insert: bool) -> None:
        if type(attempt) is not ExternalAttempt:
            raise ScientificRunStoreError("scientific_run_record_invalid")
        try:
            with self._transaction():
                if insert:
                    self._conn.execute(
                        """
                        INSERT INTO external_attempts (
                            run_id, attempt, authorization_id, binding_hash,
                            deployment_id, daemon_id, container_name, container_id,
                            state, retry_allowed, output_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        _attempt_row(attempt),
                    )
                    return
                existing = self._conn.execute(
                    "SELECT * FROM external_attempts WHERE run_id = ? AND attempt = ?",
                    (attempt.run_id, attempt.attempt),
                ).fetchone()
                if existing is None or not reservation_unchanged(
                    _attempt_from_row(existing), attempt
                ):
                    raise ScientificRunStoreError("scientific_run_record_invalid")
                updated = self._conn.execute(
                    """
                    UPDATE external_attempts
                    SET authorization_id = ?, binding_hash = ?, deployment_id = ?,
                        daemon_id = ?, container_name = ?, container_id = ?,
                        state = ?, retry_allowed = ?, output_sha256 = ?
                    WHERE run_id = ? AND attempt = ?
                    """,
                    (
                        attempt.authorization_id,
                        attempt.binding_hash,
                        attempt.deployment_id,
                        attempt.daemon_id,
                        attempt.container_name,
                        attempt.container_id,
                        attempt.state,
                        int(attempt.retry_allowed),
                        attempt.output_sha256,
                        attempt.run_id,
                        attempt.attempt,
                    ),
                )
                if updated.rowcount != 1:
                    raise ScientificRunStoreError("scientific_run_record_invalid")
        except ScientificRunStoreError:
            raise
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None

    def _external_state(self, run_id: str, attempt: int) -> str | None:
        row = self._conn.execute(
            "SELECT state FROM external_attempts WHERE run_id = ? AND attempt = ?",
            (run_id, attempt),
        ).fetchone()
        return None if row is None else row["state"]

    def _retry_blocked(self, run_id: str) -> bool:
        row = self._conn.execute(
            """
            SELECT 1 FROM external_attempts
            WHERE run_id = ? AND retry_allowed = 0 LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        return row is not None

    def _create_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE schema_meta (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                version INTEGER NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                authorization_id TEXT NOT NULL UNIQUE,
                binding_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                confirmed INTEGER NOT NULL CHECK (confirmed IN (0, 1)),
                event_count INTEGER NOT NULL,
                document TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE create_keys (
                session_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                run_id TEXT NOT NULL,
                PRIMARY KEY (session_id, idempotency_key),
                FOREIGN KEY (run_id) REFERENCES runs (run_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE retry_keys (
                session_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                run_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                snapshot TEXT,
                PRIMARY KEY (session_id, idempotency_key),
                FOREIGN KEY (run_id) REFERENCES runs (run_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE artifacts (
                artifact_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                kind TEXT NOT NULL,
                media_type TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size INTEGER NOT NULL,
                content BLOB NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs (run_id)
            )
            """
        )
        self._conn.execute("INSERT INTO schema_meta (id, version) VALUES (1, ?)", (SCHEMA_VERSION,))
        self._create_attempt_table()

    def _failpoint(self, name: str) -> None:
        if self._fail_on == name:
            raise sqlite3.IntegrityError("forced")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            assert self._conn is not None
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")


def replace_confirmed(run: ScientificRun) -> ScientificRun:
    from dataclasses import replace

    return replace(run, confirmed=True)


def _artifact_metadata(row: sqlite3.Row, artifact: ScientificArtifact) -> None:
    length = row["content_length"]
    if row["content_type"] != "blob" or type(length) is not int or isinstance(length, bool):
        raise ScientificRunStoreError("artifact_invalid")
    if length > 65536:
        raise ScientificRunStoreError("artifact_too_large")
    if length < 1:
        raise ScientificRunStoreError("artifact_invalid")
    if (
        row["attempt_type"] != "integer"
        or row["size_type"] != "integer"
        or row["digest_type"] != "text"
        or row["kind_type"] != "text"
        or row["media_type_type"] != "text"
        or type(row["attempt"]) is not int
        or isinstance(row["attempt"], bool)
        or type(row["size"]) is not int
        or isinstance(row["size"], bool)
        or type(row["sha256"]) is not str
        or type(row["kind"]) is not str
        or type(row["media_type"]) is not str
    ):
        raise ScientificRunStoreError("artifact_invalid")
    if row["kind"] != artifact.kind or row["media_type"] != artifact.media_type:
        raise ScientificRunStoreError("artifact_media_type_rejected")
    if row["attempt"] != artifact.attempt:
        raise ScientificRunStoreError("artifact_attempt_unavailable")
    if row["size"] != artifact.size or length != artifact.size or row["sha256"] != artifact.sha256:
        raise ScientificRunStoreError("artifact_digest_mismatch")


def _retry_types(row: sqlite3.Row) -> bool:
    attempt = row["attempt"]
    return (
        row["content_hash_type"] == "text"
        and type(row["content_hash"]) is str
        and row["run_id_type"] == "text"
        and type(row["run_id"]) is str
        and row["session_type"] == "text"
        and type(row["session_id"]) is str
        and row["attempt_type"] == "integer"
        and type(attempt) is int
        and not isinstance(attempt, bool)
        and row["snapshot_type"] in {"text", "null"}
        and (row["snapshot"] is None or type(row["snapshot"]) is str)
    )


def _snapshot(payload: object, row: sqlite3.Row | None = None) -> ScientificRun | None:
    if payload is None:
        return None
    run = run_from_document(payload, confirmed=True)
    if (
        not run.confirmed
        or run.status not in {"completed", "failed"}
        or row is None
        or run.run_id != row["run_id"]
        or run.session_id != row["session_id"]
        or run.attempt != row["attempt"]
    ):
        raise ScientificRunStoreError("scientific_run_record_invalid")
    return run


def _peek(path: Path) -> tuple[int | None, set[str]]:
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except (sqlite3.Error, UnicodeDecodeError):
        raise ScientificRunStoreError("scientific_run_database_unreadable") from None
    try:
        try:
            return _catalog(connection)
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificRunStoreError("scientific_run_database_unreadable") from None
    finally:
        connection.close()


def _catalog(connection: sqlite3.Connection) -> tuple[int | None, set[str]]:
    names = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    tables = names - {"sqlite_sequence"}
    if "schema_meta" not in tables:
        return None, tables
    row = connection.execute("SELECT version FROM schema_meta WHERE id = 1").fetchone()
    if row is None or type(row[0]) is not int:
        raise ScientificRunStoreError("scientific_run_schema_inconsistent")
    return int(row[0]), tables


def _database_init_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _init_guard:
        lock = _init_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _init_locks[key] = lock
        return lock


def _acquire_instance_lock(path: Path) -> None:
    canonical = path.resolve()
    key = str(canonical)
    with _instance_guard:
        current = _instance_owners.get(key)
        if current is not None and current[2] == os.getpid():
            current[1] += 1
            return
        if current is not None:
            os.close(current[0])
            del _instance_owners[key]
        try:
            canonical.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                canonical.with_name(canonical.name + ".instance.lock"),
                os.O_CREAT | os.O_RDWR | os.O_CLOEXEC,
                0o600,
            )
        except OSError:
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            if isinstance(exc, BlockingIOError) or exc.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                raise ScientificRunStoreError("scientific_run_store_busy") from None
            raise ScientificRunStoreError("scientific_run_store_unavailable") from None
        _instance_owners[key] = [descriptor, 1, os.getpid()]


def _release_instance_lock(path: Path) -> None:
    key = str(path.resolve())
    with _instance_guard:
        current = _instance_owners.get(key)
        if current is None:
            return
        if current[2] != os.getpid():
            os.close(current[0])
            del _instance_owners[key]
            return
        current[1] -= 1
        if current[1] > 0:
            return
        descriptor = current[0]
        del _instance_owners[key]
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _attempt_row(attempt: ExternalAttempt) -> tuple[object, ...]:
    return (
        attempt.run_id,
        attempt.attempt,
        attempt.authorization_id,
        attempt.binding_hash,
        attempt.deployment_id,
        attempt.daemon_id,
        attempt.container_name,
        attempt.container_id,
        attempt.state,
        int(attempt.retry_allowed),
        attempt.output_sha256,
    )


def _stored_retry_flag(value: object) -> bool:
    try:
        return retry_flag(value)
    except ApplicationContractError:
        raise ScientificRunStoreError("scientific_run_record_invalid") from None


def _attempt_from_row(row: sqlite3.Row) -> ExternalAttempt:
    return ExternalAttempt(
        run_id=row["run_id"],
        attempt=row["attempt"],
        authorization_id=row["authorization_id"],
        binding_hash=row["binding_hash"],
        deployment_id=row["deployment_id"],
        daemon_id=row["daemon_id"],
        container_name=row["container_name"],
        container_id=row["container_id"],
        state=row["state"],
        retry_allowed=_stored_retry_flag(row["retry_allowed"]),
        output_sha256=row["output_sha256"],
    )
