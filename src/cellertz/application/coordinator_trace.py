"""Durable coordinator traces.

A trace is the tool record from one question. It is not a specialist-agent
run and it is not scientific memory. An empty store is not backfilled.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

SCHEMA_VERSION = 1
_REQUIRED_TABLES = frozenset({"schema_meta", "coordinator_traces"})
_ABSENT = "No recorded coordinator trace"


class CoordinatorTraceStoreError(Exception):
    """The trace database cannot be used."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class MemoryCoordinatorTraceStore:
    """Process memory. A restart drops these traces."""

    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def append(self, document: dict[str, Any]) -> None:
        saved = json.loads(json.dumps(document))
        with self._lock:
            self._items.append(saved)

    def for_attempt(self, attempt_id: str) -> list[dict[str, Any]]:
        with self._lock:
            found = [item for item in self._items if item.get("attempt_id") == attempt_id]
        return json.loads(json.dumps(found))

    def for_request(self, request_id: str, request_revision: int) -> list[dict[str, Any]]:
        with self._lock:
            found = [
                item
                for item in self._items
                if item.get("request_id") == request_id and item.get("request_revision") == request_revision
            ]
        return json.loads(json.dumps(found))


class SQLiteCoordinatorTraceStore:
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
            raise CoordinatorTraceStoreError("coordinator_trace_schema_inconsistent")

    def _ensure_schema(self) -> None:
        assert self._conn is not None
        self._conn.executescript(
            """
            CREATE TABLE schema_meta (version INTEGER NOT NULL);
            CREATE TABLE coordinator_traces (
                trace_id TEXT PRIMARY KEY,
                attempt_id TEXT,
                conversation_id TEXT NOT NULL,
                document TEXT NOT NULL
            );
            INSERT INTO schema_meta (version) VALUES (1);
            """
        )
        self._conn.commit()

    def append(self, document: dict[str, Any]) -> None:
        payload = json.dumps(document, sort_keys=True, separators=(",", ":"))
        attempt_id = document.get("attempt_id")
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                """
                INSERT INTO coordinator_traces
                    (trace_id, attempt_id, conversation_id, document)
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(document.get("trace_id") or ""),
                    attempt_id if isinstance(attempt_id, str) else None,
                    str(document.get("conversation_id") or ""),
                    payload,
                ),
            )
            self._conn.commit()

    def for_attempt(self, attempt_id: str) -> list[dict[str, Any]]:
        with self._lock:
            assert self._conn is not None
            rows = self._conn.execute(
                """
                SELECT document FROM coordinator_traces
                WHERE attempt_id = ?
                ORDER BY rowid
                """,
                (attempt_id,),
            ).fetchall()
        loaded: list[dict[str, Any]] = []
        for row in rows:
            parsed = json.loads(row["document"])
            if isinstance(parsed, dict):
                loaded.append(parsed)
        return loaded

    def for_request(self, request_id: str, request_revision: int) -> list[dict[str, Any]]:
        loaded: list[dict[str, Any]] = []
        with self._lock:
            assert self._conn is not None
            rows = self._conn.execute(
                "SELECT document FROM coordinator_traces ORDER BY rowid"
            ).fetchall()
        for row in rows:
            parsed = json.loads(row["document"])
            if (
                isinstance(parsed, dict)
                and parsed.get("request_id") == request_id
                and parsed.get("request_revision") == request_revision
            ):
                loaded.append(parsed)
        return loaded

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


def open_coordinator_trace_store(
    environ: Mapping[str, str] | None = None,
) -> MemoryCoordinatorTraceStore | SQLiteCoordinatorTraceStore:
    """Use sqlite only when the process selects it. Memory is the default."""

    env = os.environ if environ is None else environ
    backend = env.get("CELLERTZ_COORDINATOR_TRACE_STORE", "memory").strip().lower()
    if backend == "memory":
        return MemoryCoordinatorTraceStore()
    if backend != "sqlite":
        raise CoordinatorTraceStoreError("invalid_coordinator_trace_store")
    raw = env.get("CELLERTZ_COORDINATOR_TRACE_DB", "").strip()
    if not raw or "\x00" in raw or "\n" in raw or "\r" in raw:
        raise CoordinatorTraceStoreError("coordinator_trace_db_required")
    return SQLiteCoordinatorTraceStore(Path(raw))


_HIDDEN = frozenset({"api_key", "authorization", "reasoning", "reasoning_content", "chain_of_thought"})


def trace_document(question: str, reply: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Copy the steps that ran. This does not invent a historical trace."""

    attempt_id = reply.get("attempt_id")
    accepted = reply.get("conclusion_accepted") is True
    revision = reply.get("request_revision")
    model_calls = reply.get("model_calls")
    document = {
        "trace_id": f"trace_{uuid4().hex}",
        "source_query": question,
        "execution_record": None,
        "conversation_id": reply.get("conversation_id") or "",
        "attempt_id": attempt_id if isinstance(attempt_id, str) else None,
        "request_id": reply.get("request_id") if isinstance(reply.get("request_id"), str) else None,
        "request_revision": revision if type(revision) is int else None,
        "model": reply.get("model") if isinstance(reply.get("model"), dict) else None,
        "recorded_at": datetime.now(UTC).isoformat(),
        "agent_executed": False,
        "model_calls": model_calls if type(model_calls) is int else 0,
        "usage": reply.get("usage") if isinstance(reply.get("usage"), dict) else None,
        "limits": reply.get("limits") if isinstance(reply.get("limits"), dict) else None,
        "validation": reply.get("validation") if isinstance(reply.get("validation"), dict) else None,
        "termination": reply.get("termination") if isinstance(reply.get("termination"), dict) else {
            "status": reply.get("status"),
            "reason": reply.get("text"),
        },
        "conclusion_accepted": accepted,
        "synthesis": reply.get("text") if accepted else None,
        "rejected_output": reply.get("rejected_output") if isinstance(reply.get("rejected_output"), str) else None,
        "limitations": reply.get("limitations") if isinstance(reply.get("limitations"), list) else [],
        "steps": [_public_step(step, str(reply.get("status") or "")) for step in steps],
    }
    return _scrub(document)


def absent_trace_note() -> str:
    return _ABSENT


def _public_step(step: dict[str, Any], reply_status: str) -> dict[str, Any]:
    mode = step.get("mode")
    execution = "language_model_synthesis" if mode == "language_model" else "deterministic_tool"
    status = step.get("status")
    if not isinstance(status, str):
        status = reply_status if execution == "language_model_synthesis" else "ok"
    return {
        "tool": step.get("tool"),
        "specialist_role": step.get("specialist"),
        "execution": execution,
        "agent_executed": False,
        "status": status,
        "requested_id": step.get("requested_id"),
        "resolved_attempt_id": step.get("resolved_attempt_id"),
        "summary": step.get("summary"),
        "evidence_references": step.get("evidence_references") or [],
        "rejected_output": step.get("rejected_output") if isinstance(step.get("rejected_output"), str) else None,
    }


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items() if key not in _HIDDEN}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value
