"""Durable operational store for generation plans.

The database is application state. It is not scientific memory and it
does not record a measurement. Importing this module does not create a
database. Public documents must not contain filesystem paths.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Mapping
from pathlib import Path

SCHEMA_VERSION = 1
_REQUIRED_TABLES = frozenset({"schema_meta", "plans"})


class GenerationPlanStoreError(Exception):
    """The plan database cannot be used."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SQLiteGenerationPlanStore:
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
            raise GenerationPlanStoreError("generation_plan_schema_inconsistent")

    def _ensure_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript(
            """
            CREATE TABLE schema_meta (version INTEGER NOT NULL);
            CREATE TABLE plans (
                plan_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                document TEXT NOT NULL
            );
            INSERT INTO schema_meta (version) VALUES (1);
            """
        )
        self._conn.commit()

    def save(self, plan_id: str, conversation_id: str, document: dict[str, object]) -> None:
        payload = json.dumps(document, sort_keys=True, separators=(",", ":"))
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                """
                INSERT INTO plans (plan_id, conversation_id, document)
                VALUES (?, ?, ?)
                ON CONFLICT(plan_id) DO UPDATE SET
                    conversation_id = excluded.conversation_id,
                    document = excluded.document
                """,
                (plan_id, conversation_id, payload),
            )
            self._conn.commit()

    def load_all(self) -> list[dict[str, object]]:
        with self._lock:
            assert self._conn is not None
            rows = self._conn.execute("SELECT document FROM plans ORDER BY plan_id").fetchall()
        loaded: list[dict[str, object]] = []
        for row in rows:
            parsed = json.loads(row["document"])
            if isinstance(parsed, dict):
                loaded.append(parsed)
        return loaded

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


def open_generation_plan_store(
    environ: Mapping[str, str] | None = None,
) -> SQLiteGenerationPlanStore | None:
    """Open sqlite only when the process selects it. Memory is the default."""

    env = os.environ if environ is None else environ
    backend = env.get("CELLERTZ_GENERATION_PLAN_STORE", "memory").strip().lower()
    if backend == "memory":
        return None
    if backend != "sqlite":
        raise GenerationPlanStoreError("invalid_generation_plan_store")
    raw = env.get("CELLERTZ_GENERATION_PLAN_DB", "").strip()
    if not raw or "\x00" in raw or "\n" in raw or "\r" in raw:
        raise GenerationPlanStoreError("generation_plan_db_required")
    return SQLiteGenerationPlanStore(Path(raw))
