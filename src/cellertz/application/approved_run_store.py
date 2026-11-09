"""Durable operational store for fixture runs.

The database is application state. It is not scientific memory and it
does not record scientific evidence. Importing this module does not
create a database.
"""

from __future__ import annotations

import errno
import fcntl
import os
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cellertz.application.approved_run import (
    _MAX_ARTIFACT_BYTES,
    _RETRY_QUEUED_TEXT,
    ApprovedRun,
    ApprovedRunStoreError,
    OperationalArtifact,
    _history_limit,
    _prepare_artifact_bytes,
    _visible_artifact,
    freeze_run,
    require_fixture_classification,
    run_document,
    run_from_document,
    verify_artifact_bytes,
)

APPROVED_RUN_SCHEMA_VERSION = 2
_V1_TABLES = frozenset({"schema_meta", "runs", "create_keys", "retry_keys"})
_REQUIRED_TABLES = _V1_TABLES | {"artifact_bytes"}
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _artifact_storage_length(row: sqlite3.Row) -> int:
    """Accept only a bounded BLOB. Callers fetch the bytes after this returns."""
    if row["content_type"] != "blob":
        raise ApprovedRunStoreError("artifact_invalid")
    length = row["content_length"]
    if type(length) is not int or length < 0:
        raise ApprovedRunStoreError("artifact_invalid")
    if length > _MAX_ARTIFACT_BYTES:
        raise ApprovedRunStoreError("artifact_too_large")
    size = row["size_bytes"]
    if row["size_type"] != "integer" or type(size) is not int or size < 0:
        raise ApprovedRunStoreError("artifact_invalid")
    if size > _MAX_ARTIFACT_BYTES:
        raise ApprovedRunStoreError("artifact_too_large")
    if size != length:
        raise ApprovedRunStoreError("artifact_size_mismatch")
    attempt = row["attempt"]
    if row["attempt_type"] != "integer" or type(attempt) is not int or attempt < 1:
        raise ApprovedRunStoreError("artifact_invalid")
    if row["media_type_type"] != "text" or type(row["media_type"]) is not str:
        raise ApprovedRunStoreError("artifact_invalid")
    if row["kind_type"] != "text" or type(row["kind"]) is not str:
        raise ApprovedRunStoreError("artifact_invalid")
    digest = row["content_sha256"]
    if row["digest_type"] != "text" or type(digest) is not str or _SHA256.fullmatch(digest) is None:
        raise ApprovedRunStoreError("artifact_invalid")
    return length


_instance_guard = threading.Lock()
_instance_owners: dict[str, list[int]] = {}
_init_guard = threading.Lock()
_init_locks: dict[str, threading.Lock] = {}


class SQLiteApprovedRunStore:
    """Server-owned SQLite store. One approval has one logical run.

    One operating-system process owns the resolved database file. A
    symlink is the same database. A second process, including a forked
    child, fails closed and does not recover an active run.
    """

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

    def _open(self) -> None:
        if self._path.exists():
            version, tables = _peek(self._path)
            if version is not None and version > APPROVED_RUN_SCHEMA_VERSION:
                raise ApprovedRunStoreError("unsupported_approved_run_schema")
            if version == APPROVED_RUN_SCHEMA_VERSION and _REQUIRED_TABLES <= tables:
                self._connect()
                return
            if version == 1 and _V1_TABLES <= tables and "artifact_bytes" not in tables:
                self._connect()
                self._migrate_v1()
                return
            if not (version is None and not tables):
                raise ApprovedRunStoreError("approved_run_schema_inconsistent")
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connect()
        self._ensure_schema()

    def claim_create(
        self,
        *,
        scope: tuple[str, str],
        content_hash: str,
        approval_key: tuple[str, str],
        run: ApprovedRun,
    ) -> tuple[str, ApprovedRun]:
        try:
            return self._claim_create_once(
                scope=scope,
                content_hash=content_hash,
                approval_key=approval_key,
                run=run,
            )
        except sqlite3.IntegrityError:
            try:
                return self._claim_create_once(
                    scope=scope,
                    content_hash=content_hash,
                    approval_key=approval_key,
                    run=run,
                )
            except sqlite3.IntegrityError:
                raise ApprovedRunStoreError("approved_run_store_unavailable") from None
        except sqlite3.Error:
            raise ApprovedRunStoreError("approved_run_store_unavailable") from None

    def claim_retry(
        self,
        *,
        run_id: str,
        session_id: str,
        scope: tuple[str, str],
        content_hash: str,
        expected_attempt: int,
        recorded_at: str,
    ) -> tuple[str, ApprovedRun | None]:
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
                raise ApprovedRunStoreError("approved_run_store_unavailable") from None
        except sqlite3.Error:
            raise ApprovedRunStoreError("approved_run_store_unavailable") from None

    def claim_next_queued(self, *, recorded_at: str) -> ApprovedRun | None:
        try:
            with self._transaction():
                row = self._conn.execute(
                    "SELECT run_id FROM runs WHERE status = 'queued' ORDER BY rowid LIMIT 1"
                ).fetchone()
                if row is None:
                    return None
                queued = self._load(row["run_id"])
                if queued.status != "queued":
                    raise ApprovedRunStoreError("run_record_invalid")
                running = freeze_run(
                    queued.transition(
                        "running", recorded_at=recorded_at, text="Fixture runner started."
                    )
                )
                if not self._update_run(running, "queued", queued.attempt):
                    return None
                return running
        except sqlite3.Error:
            raise ApprovedRunStoreError("approved_run_store_unavailable") from None

    def commit_transition(
        self,
        run: ApprovedRun,
        *,
        expected_status: str,
        expected_attempt: int,
    ) -> str:
        try:
            contents = {item.artifact_id: item.content for item in run.artifacts if item.content}
            with self._transaction():
                stored = freeze_run(run)
                current = self._conn.execute(
                    "SELECT status, attempt FROM runs WHERE run_id = ? AND session_id = ?",
                    (stored.run_id, stored.session_id),
                ).fetchone()
                if (
                    current is None
                    or current["status"] != expected_status
                    or current["attempt"] != expected_attempt
                ):
                    return "lost"
                self._prepare_commit_artifacts(stored, contents)
                if not self._update_run(stored, expected_status, expected_attempt):
                    return "lost"
                if stored.status == "completed":
                    self._insert_artifact_bytes(stored, contents)
                if stored.status in {"completed", "failed"}:
                    self._conn.execute(
                        """
                        UPDATE retry_keys
                        SET snapshot = ?
                        WHERE run_id = ? AND attempt = ? AND snapshot IS NULL
                        """,
                        (
                            run_document(stored),
                            stored.run_id,
                            stored.attempt,
                        ),
                    )
                return "committed"
        except sqlite3.Error:
            raise ApprovedRunStoreError("approved_run_store_unavailable") from None

    def list_runs(self, session_id: str, *, limit: int) -> tuple[ApprovedRun, ...]:
        bounded = _history_limit(limit)
        with self._lock:
            try:
                rows = self._conn.execute(
                    """
                    SELECT run_id, session_id, document, status, attempt, updated_at
                    FROM runs
                    WHERE session_id = ?
                    ORDER BY updated_at DESC, run_id DESC
                    LIMIT ?
                    """,
                    (session_id, bounded),
                ).fetchall()
            except sqlite3.Error:
                raise ApprovedRunStoreError("approved_run_store_unavailable") from None
            return tuple(self._checked(row["run_id"], row["session_id"], row) for row in rows)

    def read_artifact(
        self, session_id: str, run_id: str, artifact_id: str
    ) -> tuple[OperationalArtifact, bytes]:
        with self._lock:
            return self._read_artifact(session_id, run_id, artifact_id)

    def read_run(self, session_id: str, run_id: str) -> ApprovedRun | None:
        with self._lock:
            return self._read_run(session_id, run_id)

    def _read_run(self, session_id: str, run_id: str) -> ApprovedRun | None:
        try:
            row = self._conn.execute(
                """
                SELECT document, status, attempt, updated_at
                FROM runs
                WHERE run_id = ? AND session_id = ?
                """,
                (run_id, session_id),
            ).fetchone()
        except UnicodeDecodeError:
            raise ApprovedRunStoreError("run_record_invalid") from None
        except sqlite3.Error:
            raise ApprovedRunStoreError("approved_run_store_unavailable") from None
        if row is None:
            return None
        return self._checked(run_id, session_id, row)

    def recover_interrupted(self, *, recorded_at: str) -> tuple[ApprovedRun, ...]:
        try:
            with self._transaction():
                rows = self._conn.execute(
                    "SELECT run_id, session_id FROM runs WHERE status = 'running'"
                ).fetchall()
                recovered: list[ApprovedRun] = []
                for row in rows:
                    current = self._load(row["run_id"])
                    if current.session_id != row["session_id"] or current.status != "running":
                        raise ApprovedRunStoreError("run_record_invalid")
                    updated = freeze_run(current.mark_interrupted(recorded_at=recorded_at))
                    if not self._update_run(updated, current.status, current.attempt):
                        raise ApprovedRunStoreError("run_record_invalid")
                    self._conn.execute(
                        """
                        UPDATE retry_keys
                        SET snapshot = ?
                        WHERE run_id = ? AND attempt = ? AND snapshot IS NULL
                        """,
                        (run_document(updated), updated.run_id, updated.attempt),
                    )
                    recovered.append(updated)
                return tuple(recovered)
        except sqlite3.Error:
            raise ApprovedRunStoreError("approved_run_store_unavailable") from None

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            if self._instance_held:
                _release_instance_lock(self._path)
                self._instance_held = False

    def _claim_create_once(
        self,
        *,
        scope: tuple[str, str],
        content_hash: str,
        approval_key: tuple[str, str],
        run: ApprovedRun,
    ) -> tuple[str, ApprovedRun]:
        with self._transaction():
            found = self._lookup_create(scope, content_hash)
            if found is not None:
                return found
            existing = self._conn.execute(
                "SELECT run_id FROM runs WHERE session_id = ? AND approval_id = ?",
                (approval_key[0], approval_key[1]),
            ).fetchone()
            if existing is not None:
                self._failpoint("create_key")
                self._conn.execute(
                    """
                    INSERT INTO create_keys (session_id, idempotency_key, content_hash, run_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (scope[0], scope[1], content_hash, existing["run_id"]),
                )
                return "replay", self._load(existing["run_id"])
            stored = freeze_run(run)
            if stored.session_id != scope[0] or stored.approval_id != approval_key[1]:
                raise ApprovedRunStoreError("run_record_invalid")
            if (
                stored.session_id != approval_key[0]
                or stored.status != "queued"
                or stored.attempt != 1
            ):
                raise ApprovedRunStoreError("run_record_invalid")
            self._insert_run(stored)
            self._failpoint("create_key")
            self._conn.execute(
                """
                INSERT INTO create_keys (session_id, idempotency_key, content_hash, run_id)
                VALUES (?, ?, ?, ?)
                """,
                (scope[0], scope[1], content_hash, stored.run_id),
            )
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
    ) -> tuple[str, ApprovedRun | None]:
        with self._transaction():
            found = self._lookup_retry(scope, content_hash, session_id, run_id)
            if found is not None:
                return found
            row = self._conn.execute(
                "SELECT session_id FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None or row["session_id"] != session_id:
                return "absent", None
            current = self._load(run_id)
            if current.status != "failed" or current.attempt != expected_attempt:
                return "retry_not_allowed", current
            queued = freeze_run(
                current.transition(
                    "queued",
                    recorded_at=recorded_at,
                    text=_RETRY_QUEUED_TEXT,
                    attempt=current.attempt + 1,
                )
            )
            if not self._update_run(queued, "failed", expected_attempt):
                return "retry_not_allowed", self._load(run_id)
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
    ) -> tuple[str, ApprovedRun] | None:
        row = self._conn.execute(
            """
            SELECT content_hash, run_id
            FROM create_keys
            WHERE session_id = ? AND idempotency_key = ?
            """,
            (scope[0], scope[1]),
        ).fetchone()
        if row is None:
            return None
        stored = self._load(row["run_id"])
        outcome = "replay" if row["content_hash"] == content_hash else "conflict"
        return outcome, stored

    def _lookup_retry(
        self,
        scope: tuple[str, str],
        content_hash: str,
        session_id: str,
        run_id: str,
    ) -> tuple[str, ApprovedRun | None] | None:
        row = self._conn.execute(
            """
            SELECT content_hash, run_id, attempt, snapshot
            FROM retry_keys
            WHERE session_id = ? AND idempotency_key = ?
            """,
            (scope[0], scope[1]),
        ).fetchone()
        if row is None:
            return None
        if row["run_id"] != run_id:
            current = self._load(row["run_id"])
            return "conflict", _snapshot(row["snapshot"], current)
        current = self._load(run_id)
        if current.session_id != session_id:
            return "absent", None
        snapshot = _snapshot(row["snapshot"], current)
        if row["content_hash"] != content_hash:
            return "conflict", snapshot
        if row["snapshot"] is not None:
            return "replay", snapshot
        if current.attempt != row["attempt"]:
            raise ApprovedRunStoreError("run_record_invalid")
        return "replay", current

    def _insert_run(self, run: ApprovedRun) -> None:
        self._conn.execute(
            """
            INSERT INTO runs (
                run_id, session_id, approval_id, status, attempt, updated_at, document
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_id,
                run.session_id,
                run.approval_id,
                run.status,
                run.attempt,
                run.updated_at,
                run_document(run),
            ),
        )

    def _update_run(self, run: ApprovedRun, expected_status: str, expected_attempt: int) -> bool:
        cursor = self._conn.execute(
            """
            UPDATE runs
            SET status = ?, attempt = ?, updated_at = ?, document = ?
            WHERE run_id = ? AND session_id = ? AND status = ? AND attempt = ?
            """,
            (
                run.status,
                run.attempt,
                run.updated_at,
                run_document(run),
                run.run_id,
                run.session_id,
                expected_status,
                expected_attempt,
            ),
        )
        return cursor.rowcount == 1

    def _load(self, run_id: str) -> ApprovedRun:
        try:
            row = self._conn.execute(
                "SELECT session_id, status, attempt, updated_at, document FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        except UnicodeDecodeError:
            raise ApprovedRunStoreError("run_record_invalid") from None
        if row is None:
            raise ApprovedRunStoreError("run_record_invalid")
        return self._checked(run_id, row["session_id"], row)

    def _checked(self, run_id: str, session_id: str, row: sqlite3.Row) -> ApprovedRun:
        try:
            payload = row["document"]
        except UnicodeDecodeError:
            raise ApprovedRunStoreError("run_record_invalid") from None
        run = run_from_document(payload)
        if run.run_id != run_id or run.session_id != session_id:
            raise ApprovedRunStoreError("run_record_invalid")
        if run.status != row["status"] or run.attempt != row["attempt"]:
            raise ApprovedRunStoreError("run_record_invalid")
        updated_at = row["updated_at"]
        if type(updated_at) is not str or updated_at != run.updated_at:
            raise ApprovedRunStoreError("run_record_invalid")
        return run

    def _read_artifact(
        self, session_id: str, run_id: str, artifact_id: str
    ) -> tuple[OperationalArtifact, bytes]:
        run = self._read_run(session_id, run_id)
        if run is None:
            raise ApprovedRunStoreError("run_absent")
        artifact = _visible_artifact(run, artifact_id)
        try:
            row = self._conn.execute(
                """
                SELECT typeof(content) AS content_type,
                       length(content) AS content_length,
                       typeof(size_bytes) AS size_type,
                       size_bytes,
                       typeof(attempt) AS attempt_type,
                       attempt,
                       typeof(media_type) AS media_type_type,
                       media_type,
                       typeof(kind) AS kind_type,
                       kind,
                       typeof(content_sha256) AS digest_type,
                       content_sha256
                FROM artifact_bytes
                WHERE artifact_id = ? AND run_id = ?
                """,
                (artifact_id, run_id),
            ).fetchone()
        except sqlite3.Error:
            raise ApprovedRunStoreError("approved_run_store_unavailable") from None
        if row is None:
            raise ApprovedRunStoreError("artifact_absent")
        length = _artifact_storage_length(row)
        if row["media_type"] != artifact.media_type or row["kind"] != artifact.kind:
            raise ApprovedRunStoreError("artifact_media_type_rejected")
        if row["attempt"] != run.attempt:
            raise ApprovedRunStoreError("artifact_attempt_unavailable")
        if row["size_bytes"] != artifact.size_bytes or length != artifact.size_bytes:
            raise ApprovedRunStoreError("artifact_size_mismatch")
        if row["content_sha256"] != artifact.content_sha256:
            raise ApprovedRunStoreError("artifact_digest_mismatch")
        try:
            stored = self._conn.execute(
                """
                SELECT content
                FROM artifact_bytes
                WHERE artifact_id = ? AND run_id = ?
                """,
                (artifact_id, run_id),
            ).fetchone()
        except sqlite3.Error:
            raise ApprovedRunStoreError("approved_run_store_unavailable") from None
        content = None if stored is None else stored["content"]
        if type(content) is not bytes or len(content) != length:
            raise ApprovedRunStoreError("artifact_invalid")
        parsed = verify_artifact_bytes(artifact, content)
        require_fixture_classification(artifact, content, parsed)
        return artifact, content

    def _prepare_commit_artifacts(
        self, run: ApprovedRun, contents: dict[str, bytes]
    ) -> dict[str, bytes]:
        existing: set[str] = set()
        for artifact in run.artifacts:
            row = self._conn.execute(
                "SELECT artifact_id FROM artifact_bytes WHERE artifact_id = ?",
                (artifact.artifact_id,),
            ).fetchone()
            if row is not None:
                existing.add(row["artifact_id"])
        return _prepare_artifact_bytes(run, contents, existing)

    def _insert_artifact_bytes(self, run: ApprovedRun, contents: dict[str, bytes]) -> None:
        prepared = self._prepare_commit_artifacts(run, contents)
        for artifact in run.artifacts:
            content = prepared.get(artifact.artifact_id)
            if content is None:
                continue
            self._conn.execute(
                """
                INSERT INTO artifact_bytes (
                    artifact_id, run_id, attempt, kind, media_type,
                    size_bytes, content_sha256, content
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact.artifact_id,
                    run.run_id,
                    run.attempt,
                    artifact.kind,
                    artifact.media_type,
                    artifact.size_bytes,
                    artifact.content_sha256,
                    content,
                ),
            )
            self._failpoint("artifact_bytes")

    def _connect(self) -> None:
        # ponytail: one lock per database file; concurrent constructors in this process
        # must not race PRAGMA journal_mode=WAL on a database that does not exist yet.
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
        except sqlite3.Error:
            raise ApprovedRunStoreError("approved_run_store_unavailable") from None

    def _ensure_schema(self) -> None:
        try:
            with self._transaction():
                version, tables = _catalog(self._conn)
                if version == APPROVED_RUN_SCHEMA_VERSION and _REQUIRED_TABLES <= tables:
                    return
                if version is not None and version > APPROVED_RUN_SCHEMA_VERSION:
                    raise ApprovedRunStoreError("unsupported_approved_run_schema")
                if version is None and not tables:
                    self._create_tables()
                    return
                raise ApprovedRunStoreError("approved_run_schema_inconsistent")
        except sqlite3.Error:
            raise ApprovedRunStoreError("approved_run_store_unavailable") from None

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
                approval_id TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                document TEXT NOT NULL,
                UNIQUE (session_id, approval_id)
            )
            """
        )
        self._create_artifact_table()
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
            "INSERT INTO schema_meta (id, version) VALUES (1, ?)",
            (APPROVED_RUN_SCHEMA_VERSION,),
        )

    def _create_artifact_table(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE artifact_bytes (
                artifact_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                kind TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                content_sha256 TEXT NOT NULL,
                content BLOB NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs (run_id)
            )
            """
        )

    def _migrate_v1(self) -> None:
        try:
            with self._transaction():
                version, tables = _catalog(self._conn)
                if version == APPROVED_RUN_SCHEMA_VERSION and _REQUIRED_TABLES <= tables:
                    return
                if version != 1 or not _V1_TABLES <= tables or "artifact_bytes" in tables:
                    raise ApprovedRunStoreError("approved_run_schema_inconsistent")
                self._conn.execute(
                    "ALTER TABLE runs ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''"
                )
                rows = self._conn.execute("SELECT run_id, document FROM runs").fetchall()
                for row in rows:
                    stored = run_from_document(row["document"])
                    if stored.run_id != row["run_id"]:
                        raise ApprovedRunStoreError("run_record_invalid")
                    self._conn.execute(
                        "UPDATE runs SET updated_at = ? WHERE run_id = ?",
                        (stored.updated_at, stored.run_id),
                    )
                self._create_artifact_table()
                self._conn.execute(
                    "UPDATE schema_meta SET version = ? WHERE id = 1",
                    (APPROVED_RUN_SCHEMA_VERSION,),
                )
        except ApprovedRunStoreError:
            raise
        except sqlite3.Error:
            raise ApprovedRunStoreError("approved_run_store_unavailable") from None

    def _failpoint(self, name: str) -> None:
        if self._fail_on == name:
            raise sqlite3.IntegrityError("forced")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")


def _peek(path: Path) -> tuple[int | None, set[str]]:
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        raise ApprovedRunStoreError("approved_run_database_unreadable") from None
    try:
        try:
            return _catalog(conn)
        except sqlite3.Error:
            raise ApprovedRunStoreError("approved_run_database_unreadable") from None
    finally:
        conn.close()


def _catalog(conn: sqlite3.Connection) -> tuple[int | None, set[str]]:
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    tables = names - {"sqlite_sequence"}
    if "schema_meta" not in tables:
        return None, tables
    row = conn.execute("SELECT version FROM schema_meta WHERE id = 1").fetchone()
    if row is None or type(row[0]) is not int:
        raise ApprovedRunStoreError("approved_run_schema_inconsistent")
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
            # Forked child: drop the inherited descriptor without unlocking.
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
            raise ApprovedRunStoreError("approved_run_store_unavailable") from None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            if isinstance(exc, BlockingIOError) or exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise ApprovedRunStoreError("approved_run_store_busy") from None
            raise ApprovedRunStoreError("approved_run_store_unavailable") from None
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


def _snapshot(payload: str | None, current: ApprovedRun) -> ApprovedRun:
    if payload is None:
        return current
    snapshot = run_from_document(payload)
    if snapshot.run_id != current.run_id:
        raise ApprovedRunStoreError("run_record_invalid")
    return snapshot
