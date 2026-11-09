"""Durable Supervisor analyses.

This database is separate from the design-request drafts. Opening it does
not create, revise, or read a draft row. An analysis is inserted once.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol


class SupervisorAnalysisStore(Protocol):
    def append(self, document: dict[str, Any]) -> None: ...

    def list_for(self, request_id: str) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


class SQLiteSupervisorAnalysisStore:
    """One connection owns the analysis file beside the draft database."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path).resolve()
        self._lock = threading.RLock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS design_supervisor_analyses (
                analysis_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                analyzed_at TEXT NOT NULL,
                document TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def append(self, document: dict[str, Any]) -> None:
        analysis_id = document.get("analysis_id")
        request_id = document.get("request_id")
        revision = document.get("revision")
        analyzed_at = document.get("analyzed_at")
        if not isinstance(analysis_id, str) or not analysis_id.strip():
            raise ValueError("invalid_analysis")
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("invalid_analysis")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("invalid_analysis")
        if not isinstance(analyzed_at, str) or not analyzed_at.strip():
            raise ValueError("invalid_analysis")
        payload = json.dumps(document, sort_keys=True, separators=(",", ":"))
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO design_supervisor_analyses
                    (analysis_id, request_id, revision, analyzed_at, document)
                VALUES (?, ?, ?, ?, ?)
                """,
                (analysis_id, request_id, revision, analyzed_at, payload),
            )
            self._conn.commit()

    def list_for(self, request_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT document FROM design_supervisor_analyses
                WHERE request_id = ?
                ORDER BY analyzed_at DESC, analysis_id DESC
                """,
                (request_id,),
            ).fetchall()
        loaded: list[dict[str, Any]] = []
        for row in rows:
            document = json.loads(row["document"])
            if isinstance(document, dict) and document.get("request_id") == request_id:
                loaded.append(document)
        return loaded

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def open_supervisor_analysis_store(
    environ: Mapping[str, str] | None = None,
) -> SQLiteSupervisorAnalysisStore | None:
    """Open the sibling file only when drafts themselves use sqlite."""

    env = os.environ if environ is None else environ
    if env.get("CELLERTZ_DESIGN_DRAFT_STORE", "memory").strip().lower() != "sqlite":
        return None
    raw = env.get("CELLERTZ_DESIGN_DRAFT_DB", "").strip()
    if not raw or "\x00" in raw or "\n" in raw or "\r" in raw:
        return None
    return SQLiteSupervisorAnalysisStore(Path(raw).with_name("design-supervisor-analyses.sqlite"))
