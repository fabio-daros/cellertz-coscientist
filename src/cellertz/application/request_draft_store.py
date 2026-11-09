"""Durable revisions of a protein design request draft.

The database is application state for POST /api/design-draft. It does not
store a journey, a generation plan, or a run, and it is not scientific
memory. Importing this module does not create a database. A revision row
is inserted once and is never replaced.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

SCHEMA_VERSION = 1
_REQUIRED_TABLES = frozenset({"schema_meta", "design_request_drafts"})


class DesignDraftStoreError(Exception):
    """The draft database cannot be used."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DraftRevisionConflict(Exception):
    """The stored revision moved. The new row was not written."""


class DesignDraftStore(Protocol):
    def load(self) -> list[dict[str, Any]]: ...

    def revisions(self, request_id: str) -> list[dict[str, Any]]: ...

    def append(self, document: dict[str, Any]) -> None: ...

    def close(self) -> None: ...


class SQLiteDesignDraftStore:
    """One connection owns the resolved database file."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path).resolve()
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._open()

    def _open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        self._conn = conn
        version = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'"
        ).fetchone()
        if version is None:
            self._ensure_schema()
            return
        row = conn.execute("SELECT version FROM schema_meta").fetchone()
        tables = {
            item["name"]
            for item in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if row is None or int(row["version"]) != SCHEMA_VERSION or not _REQUIRED_TABLES <= tables:
            raise DesignDraftStoreError("design_draft_schema_inconsistent")

    def _ensure_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript(
            """
            CREATE TABLE schema_meta (version INTEGER NOT NULL);
            CREATE TABLE design_request_drafts (
                request_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                document TEXT NOT NULL,
                PRIMARY KEY (request_id, revision)
            );
            INSERT INTO schema_meta (version) VALUES (1);
            """
        )
        self._conn.commit()

    def load(self) -> list[dict[str, Any]]:
        with self._lock:
            assert self._conn is not None
            rows = self._conn.execute(
                """
                SELECT document FROM design_request_drafts
                ORDER BY request_id, revision
                """
            ).fetchall()
        return [_document(row["document"]) for row in rows]

    def revisions(self, request_id: str) -> list[dict[str, Any]]:
        with self._lock:
            assert self._conn is not None
            rows = self._conn.execute(
                """
                SELECT document FROM design_request_drafts
                WHERE request_id = ?
                ORDER BY revision
                """,
                (request_id,),
            ).fetchall()
        return [_document(row["document"]) for row in rows]

    def append(self, document: dict[str, Any]) -> None:
        draft = document.get("draft")
        if not isinstance(draft, dict):
            raise DesignDraftStoreError("invalid_stored_draft")
        request_id = draft.get("request_id")
        revision = draft.get("revision")
        if (
            not isinstance(request_id, str)
            or not request_id
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
        ):
            raise DesignDraftStoreError("invalid_stored_draft")
        payload = json.dumps(document, sort_keys=True, separators=(",", ":"))
        with self._lock:
            assert self._conn is not None
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute(
                    """
                    SELECT MAX(revision) AS revision
                    FROM design_request_drafts
                    WHERE request_id = ?
                    """,
                    (request_id,),
                ).fetchone()
                current = 0 if row is None or row["revision"] is None else int(row["revision"])
                if revision != current + 1:
                    self._conn.rollback()
                    raise DraftRevisionConflict()
                self._conn.execute(
                    """
                    INSERT INTO design_request_drafts (request_id, revision, document)
                    VALUES (?, ?, ?)
                    """,
                    (request_id, revision, payload),
                )
                self._conn.commit()
            except DraftRevisionConflict:
                raise
            except sqlite3.IntegrityError:
                self._conn.rollback()
                raise DraftRevisionConflict() from None
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


def open_design_draft_store(
    environ: Mapping[str, str] | None = None,
) -> SQLiteDesignDraftStore | None:
    """Open sqlite only when the process selects it. Memory is the default."""

    env = os.environ if environ is None else environ
    backend = env.get("CELLERTZ_DESIGN_DRAFT_STORE", "memory").strip().lower()
    if backend == "memory":
        return None
    if backend != "sqlite":
        raise DesignDraftStoreError("invalid_design_draft_store")
    raw = env.get("CELLERTZ_DESIGN_DRAFT_DB", "").strip()
    if not raw or "\x00" in raw or "\n" in raw or "\r" in raw:
        raise DesignDraftStoreError("design_draft_db_required")
    return SQLiteDesignDraftStore(Path(raw))


def _document(payload: str) -> dict[str, Any]:
    parsed = json.loads(payload)
    if not isinstance(parsed, dict):
        raise DesignDraftStoreError("invalid_stored_draft")
    return parsed
