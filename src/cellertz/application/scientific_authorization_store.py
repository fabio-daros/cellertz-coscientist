"""Durable operational store for scientific-run authorization.

The database is application state. It is not scientific memory and it
does not record scientific evidence. Importing this module does not
create a database.
"""

from __future__ import annotations

import errno
import fcntl
import os
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from cellertz.application.contracts import ApplicationContractError
from cellertz.application.scientific_authorization import (
    ScientificAuthorizationStoreError,
    ScientificRunAuthorization,
    _revoke_record,
    authorization_from_stored,
    stored_json,
)

SCHEMA_VERSION = 1
_REQUIRED_TABLES = frozenset({"schema_meta", "authorizations", "events", "idempotency_keys"})
_instance_guard = threading.Lock()
_instance_owners: dict[str, list[int]] = {}
_init_guard = threading.Lock()
_init_locks: dict[str, threading.Lock] = {}


class SQLiteScientificAuthorizationStore:
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

    def _open(self) -> None:
        if self._path.exists():
            version, tables = _peek(self._path)
            if version is not None and version > SCHEMA_VERSION:
                raise ScientificAuthorizationStoreError(
                    "unsupported_scientific_authorization_schema"
                )
            if version == SCHEMA_VERSION and _REQUIRED_TABLES <= tables:
                self._connect()
                return
            if version is None and not tables:
                self._connect()
                self._ensure_schema()
                return
            raise ScientificAuthorizationStoreError("scientific_authorization_schema_inconsistent")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._connect()
        self._ensure_schema()

    def claim(
        self,
        *,
        scope: tuple[str, str],
        content_hash: str,
        binding_hash: str,
        authorization: ScientificRunAuthorization,
    ) -> tuple[str, ScientificRunAuthorization]:
        if authorization.binding_hash != binding_hash or authorization.session_id != scope[0]:
            raise ScientificAuthorizationStoreError("authorization_record_invalid")
        try:
            return self._claim_once(
                scope=scope,
                content_hash=content_hash,
                binding_hash=binding_hash,
                authorization=authorization,
            )
        except sqlite3.IntegrityError:
            try:
                return self._claim_once(
                    scope=scope,
                    content_hash=content_hash,
                    binding_hash=binding_hash,
                    authorization=authorization,
                )
            except sqlite3.IntegrityError:
                raise ScientificAuthorizationStoreError(
                    "scientific_authorization_store_unavailable"
                ) from None
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificAuthorizationStoreError(
                "scientific_authorization_store_unavailable"
            ) from None

    def revoke(
        self,
        *,
        session_id: str,
        authorization_id: str,
        scope: tuple[str, str],
        content_hash: str,
        recorded_at: str,
        text: str,
    ) -> tuple[str, ScientificRunAuthorization | None]:
        try:
            return self._revoke_once(
                session_id=session_id,
                authorization_id=authorization_id,
                scope=scope,
                content_hash=content_hash,
                recorded_at=recorded_at,
                text=text,
            )
        except sqlite3.IntegrityError:
            try:
                return self._revoke_once(
                    session_id=session_id,
                    authorization_id=authorization_id,
                    scope=scope,
                    content_hash=content_hash,
                    recorded_at=recorded_at,
                    text=text,
                )
            except sqlite3.IntegrityError:
                raise ScientificAuthorizationStoreError(
                    "scientific_authorization_store_unavailable"
                ) from None
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificAuthorizationStoreError(
                "scientific_authorization_store_unavailable"
            ) from None

    def read(self, session_id: str, authorization_id: str) -> ScientificRunAuthorization | None:
        try:
            with self._lock:
                return self._load(session_id, authorization_id)
        except ApplicationContractError:
            raise ScientificAuthorizationStoreError("authorization_record_invalid") from None
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificAuthorizationStoreError(
                "scientific_authorization_store_unavailable"
            ) from None

    def list_authorizations(
        self, session_id: str, *, limit: int
    ) -> tuple[ScientificRunAuthorization, ...]:
        try:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT authorization_id FROM authorizations
                    WHERE session_id = ?
                    ORDER BY updated_at DESC, authorization_id DESC
                    LIMIT ?
                    """,
                    (session_id, limit),
                ).fetchall()
                return tuple(
                    self._require(self._load(session_id, row["authorization_id"])) for row in rows
                )
        except ApplicationContractError:
            raise ScientificAuthorizationStoreError("authorization_record_invalid") from None
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificAuthorizationStoreError(
                "scientific_authorization_store_unavailable"
            ) from None

    def claim_consumption(
        self,
        *,
        session_id: str,
        authorization_id: str,
        expected_binding_hash: str,
        consumed_at: str,
        recheck: Callable[[], str | None] | None = None,
    ) -> str:
        try:
            with self._transaction():
                current = self._conn.execute(
                    """
                    SELECT status, consumed_at, binding_hash FROM authorizations
                    WHERE authorization_id = ? AND session_id = ?
                    """,
                    (authorization_id, session_id),
                ).fetchone()
                if (
                    current is None
                    or type(current["status"]) is not str
                    or current["status"] != "authorized"
                    or current["consumed_at"] is not None
                    or type(current["binding_hash"]) is not str
                ):
                    return "unavailable"
                expected = expected_binding_hash if recheck is None else recheck()
                if (
                    type(expected) is not str
                    or current["binding_hash"] != expected
                    or expected != expected_binding_hash
                ):
                    return "unavailable"
                self._conn.execute(
                    """
                    UPDATE authorizations SET consumed_at = ?
                    WHERE authorization_id = ? AND session_id = ?
                      AND status = 'authorized' AND consumed_at IS NULL
                      AND binding_hash = ?
                    """,
                    (consumed_at, authorization_id, session_id, expected_binding_hash),
                )
                if self._conn.execute("SELECT changes()").fetchone()[0] != 1:
                    return "unavailable"
                return "consumed"
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificAuthorizationStoreError(
                "scientific_authorization_store_unavailable"
            ) from None

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            if self._instance_held:
                _release_instance_lock(self._path)
                self._instance_held = False

    def _claim_once(
        self,
        *,
        scope: tuple[str, str],
        content_hash: str,
        binding_hash: str,
        authorization: ScientificRunAuthorization,
    ) -> tuple[str, ScientificRunAuthorization]:
        with self._transaction():
            found = self._lookup_key(scope, content_hash)
            if found is not None:
                return found
            existing = self._conn.execute(
                """
                SELECT authorization_id FROM authorizations
                WHERE session_id = ? AND binding_hash = ?
                """,
                (scope[0], binding_hash),
            ).fetchone()
            if existing is not None:
                self._insert_key(scope, content_hash, existing["authorization_id"])
                self._failpoint("key")
                record = self._require(self._load(scope[0], existing["authorization_id"]))
                return "replay", record
            self._insert_record(authorization)
            self._failpoint("authorization")
            self._insert_events(authorization)
            self._failpoint("event")
            self._insert_key(scope, content_hash, authorization.authorization_id)
            self._failpoint("key")
            return "created", authorization

    def _revoke_once(
        self,
        *,
        session_id: str,
        authorization_id: str,
        scope: tuple[str, str],
        content_hash: str,
        recorded_at: str,
        text: str,
    ) -> tuple[str, ScientificRunAuthorization | None]:
        with self._transaction():
            found = self._lookup_key(scope, content_hash)
            if found is not None:
                outcome, record = found
                if record.authorization_id != authorization_id:
                    return "conflict", record
                return outcome, record
            current = self._load(session_id, authorization_id)
            if current is None:
                return "absent", None
            if current.consumed_at is not None:
                return "consumed", current
            if current.status == "revoked":
                self._insert_key(scope, content_hash, authorization_id)
                return "replay", current
            revoked = _revoke_record(current, recorded_at=recorded_at, text=text)
            self._conn.execute(
                """
                UPDATE authorizations
                SET status = ?, document = ?, updated_at = ?
                WHERE authorization_id = ? AND session_id = ? AND status = 'authorized'
                """,
                (
                    revoked.status,
                    stored_json(revoked),
                    revoked.updated_at,
                    authorization_id,
                    session_id,
                ),
            )
            if self._conn.execute("SELECT changes()").fetchone()[0] != 1:
                raise ScientificAuthorizationStoreError("authorization_record_invalid")
            self._insert_events(revoked, only_new=True)
            self._failpoint("event")
            self._insert_key(scope, content_hash, authorization_id)
            self._failpoint("key")
            return "revoked", revoked

    def _lookup_key(
        self, scope: tuple[str, str], content_hash: str
    ) -> tuple[str, ScientificRunAuthorization] | None:
        row = self._conn.execute(
            """
            SELECT content_hash, authorization_id FROM idempotency_keys
            WHERE session_id = ? AND idempotency_key = ?
            """,
            scope,
        ).fetchone()
        if row is None:
            return None
        record = self._require(self._load(scope[0], row["authorization_id"]))
        if row["content_hash"] != content_hash:
            return "conflict", record
        return "replay", record

    def _load(self, session_id: str, authorization_id: str) -> ScientificRunAuthorization | None:
        row = self._conn.execute(
            """
            SELECT authorization_id, session_id, binding_hash, status, consumed_at,
                   document, created_at, updated_at
            FROM authorizations
            WHERE authorization_id = ? AND session_id = ?
            """,
            (authorization_id, session_id),
        ).fetchone()
        if row is None:
            return None
        kinds = self._conn.execute(
            """
            SELECT typeof(authorization_id), typeof(session_id), typeof(binding_hash),
                   typeof(status), typeof(consumed_at), typeof(document),
                   typeof(created_at), typeof(updated_at)
            FROM authorizations
            WHERE authorization_id = ? AND session_id = ?
            """,
            (authorization_id, session_id),
        ).fetchone()
        consumed_kind = "null" if row["consumed_at"] is None else "text"
        if kinds is None or tuple(kinds) != (
            "text",
            "text",
            "text",
            "text",
            consumed_kind,
            "text",
            "text",
            "text",
        ):
            raise ScientificAuthorizationStoreError("authorization_record_invalid")
        if any(
            type(row[name]) is not str
            for name in (
                "authorization_id",
                "session_id",
                "binding_hash",
                "status",
                "document",
                "created_at",
                "updated_at",
            )
        ):
            raise ScientificAuthorizationStoreError("authorization_record_invalid")
        if row["consumed_at"] is not None and type(row["consumed_at"]) is not str:
            raise ScientificAuthorizationStoreError("authorization_record_invalid")
        try:
            record = authorization_from_stored(
                row["document"],
                binding_hash=row["binding_hash"],
                consumed_at=row["consumed_at"],
            )
        except (ApplicationContractError, TypeError, ValueError):
            raise ScientificAuthorizationStoreError("authorization_record_invalid") from None
        if (
            record.authorization_id != row["authorization_id"]
            or record.session_id != row["session_id"]
            or record.status != row["status"]
            or record.created_at != row["created_at"]
            or record.updated_at != row["updated_at"]
        ):
            raise ScientificAuthorizationStoreError("authorization_record_invalid")
        events = self._conn.execute(
            """
            SELECT sequence, status, recorded_at, text, origin
            FROM events WHERE authorization_id = ? ORDER BY sequence
            """,
            (authorization_id,),
        ).fetchall()
        if len(events) != len(record.events):
            raise ScientificAuthorizationStoreError("authorization_record_invalid")
        for row_event, event in zip(events, record.events, strict=True):
            event_kinds = self._conn.execute(
                """
                SELECT typeof(sequence), typeof(status), typeof(recorded_at),
                       typeof(text), typeof(origin)
                FROM events
                WHERE authorization_id = ? AND sequence = ?
                """,
                (authorization_id, event.sequence),
            ).fetchone()
            if event_kinds is None or tuple(event_kinds) != (
                "integer",
                "text",
                "text",
                "text",
                "text",
            ):
                raise ScientificAuthorizationStoreError("authorization_record_invalid")
            if (
                type(row_event["sequence"]) is not int
                or isinstance(row_event["sequence"], bool)
                or type(row_event["status"]) is not str
                or type(row_event["recorded_at"]) is not str
                or type(row_event["text"]) is not str
                or type(row_event["origin"]) is not str
                or row_event["sequence"] != event.sequence
                or row_event["status"] != event.status
                or row_event["recorded_at"] != event.recorded_at
                or row_event["text"] != event.text
                or row_event["origin"] != event.origin
            ):
                raise ScientificAuthorizationStoreError("authorization_record_invalid")
        return record

    def _insert_record(self, authorization: ScientificRunAuthorization) -> None:
        self._conn.execute(
            """
            INSERT INTO authorizations (
                authorization_id, session_id, binding_hash, status, consumed_at,
                document, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                authorization.authorization_id,
                authorization.session_id,
                authorization.binding_hash,
                authorization.status,
                authorization.consumed_at,
                stored_json(authorization),
                authorization.created_at,
                authorization.updated_at,
            ),
        )

    def _insert_events(
        self, authorization: ScientificRunAuthorization, *, only_new: bool = False
    ) -> None:
        events = authorization.events[-1:] if only_new else authorization.events
        for event in events:
            self._conn.execute(
                """
                INSERT INTO events (
                    authorization_id, sequence, status, recorded_at, text, origin
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    authorization.authorization_id,
                    event.sequence,
                    event.status,
                    event.recorded_at,
                    event.text,
                    event.origin,
                ),
            )

    def _insert_key(self, scope: tuple[str, str], content_hash: str, authorization_id: str) -> None:
        self._conn.execute(
            """
            INSERT INTO idempotency_keys (
                session_id, idempotency_key, content_hash, authorization_id
            ) VALUES (?, ?, ?, ?)
            """,
            (scope[0], scope[1], content_hash, authorization_id),
        )

    def _require(self, record: ScientificRunAuthorization | None) -> ScientificRunAuthorization:
        if record is None:
            raise ScientificAuthorizationStoreError("authorization_record_invalid")
        return record

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
            raise ScientificAuthorizationStoreError(
                "scientific_authorization_store_unavailable"
            ) from None

    def _ensure_schema(self) -> None:
        try:
            with self._transaction():
                version, tables = _catalog(self._conn)
                if version == SCHEMA_VERSION and _REQUIRED_TABLES <= tables:
                    return
                if version is not None and version > SCHEMA_VERSION:
                    raise ScientificAuthorizationStoreError(
                        "unsupported_scientific_authorization_schema"
                    )
                if version is None and not tables:
                    self._create_tables()
                    return
                raise ScientificAuthorizationStoreError(
                    "scientific_authorization_schema_inconsistent"
                )
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificAuthorizationStoreError(
                "scientific_authorization_store_unavailable"
            ) from None

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
            CREATE TABLE authorizations (
                authorization_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                binding_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                consumed_at TEXT,
                document TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (session_id, binding_hash)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE events (
                authorization_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                status TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                text TEXT NOT NULL,
                origin TEXT NOT NULL,
                PRIMARY KEY (authorization_id, sequence),
                FOREIGN KEY (authorization_id) REFERENCES authorizations (authorization_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE idempotency_keys (
                session_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                authorization_id TEXT NOT NULL,
                PRIMARY KEY (session_id, idempotency_key),
                FOREIGN KEY (authorization_id) REFERENCES authorizations (authorization_id)
            )
            """
        )
        self._conn.execute(
            "INSERT INTO schema_meta (id, version) VALUES (1, ?)",
            (SCHEMA_VERSION,),
        )

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
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except (sqlite3.Error, UnicodeDecodeError):
        raise ScientificAuthorizationStoreError(
            "scientific_authorization_database_unreadable"
        ) from None
    try:
        try:
            return _catalog(connection)
        except (sqlite3.Error, UnicodeDecodeError):
            raise ScientificAuthorizationStoreError(
                "scientific_authorization_database_unreadable"
            ) from None
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
        raise ScientificAuthorizationStoreError("scientific_authorization_schema_inconsistent")
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
            raise ScientificAuthorizationStoreError(
                "scientific_authorization_store_unavailable"
            ) from None
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            if isinstance(exc, BlockingIOError) or exc.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                raise ScientificAuthorizationStoreError(
                    "scientific_authorization_store_busy"
                ) from None
            raise ScientificAuthorizationStoreError(
                "scientific_authorization_store_unavailable"
            ) from None
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
