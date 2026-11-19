"""Which stored prompt versions a recorded result names.

A version that produced a result cannot be removed without making that result
unexplainable: the analysis says it was written under version 2 and there
would be no version 2 to read. This module answers only the question "is this
version named anywhere", so a host can refuse exactly those and no more.

It reads the recorded documents rather than any index, because the reference
lives inside the document the science wrote.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path

# (file beside the draft database, table, column holding the JSON document)
_SOURCES = (
    ("design-supervisor-analyses.sqlite", "design_supervisor_analyses", "document"),
    ("design-plan-suggestions.sqlite", "design_plan_suggestions", "document"),
    ("coordinator-traces.sqlite", "coordinator_traces", "document"),
)


def referenced_versions(environ: Mapping[str, str] | None = None) -> set[tuple[str, int]]:
    """Every (agent_key, version) a recorded analysis or suggestion names."""

    env = os.environ if environ is None else environ
    if env.get("CELLERTZ_DESIGN_DRAFT_STORE", "memory").strip().lower() != "sqlite":
        return set()
    raw = env.get("CELLERTZ_DESIGN_DRAFT_DB", "").strip()
    if not raw or any(char in raw for char in "\x00\n\r"):
        return set()
    found: set[tuple[str, int]] = set()
    for filename, table, column in _SOURCES:
        path = Path(raw).with_name(filename)
        if not path.is_file():
            continue
        found |= _from_store(path, table, column)
    return found


def _from_store(path: Path, table: str, column: str) -> set[tuple[str, int]]:
    found: set[tuple[str, int]] = set()
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return found
    try:
        rows = connection.execute(f"SELECT {column} FROM {table}").fetchall()
    except sqlite3.Error:
        return found
    finally:
        connection.close()
    for (document,) in rows:
        try:
            parsed = json.loads(document)
        except (TypeError, ValueError):
            continue
        found |= _from_document(parsed)
    return found


def _from_document(document: object) -> set[tuple[str, int]]:
    if not isinstance(document, dict):
        return set()
    candidates = []
    one = document.get("prompt")
    if isinstance(one, dict):
        candidates.append(one)
    many = document.get("prompts")
    if isinstance(many, list):
        candidates.extend(item for item in many if isinstance(item, dict))
    found: set[tuple[str, int]] = set()
    for item in candidates:
        key = item.get("agent_key")
        version = item.get("version")
        # A built-in origin records no version, and there is nothing to keep.
        if isinstance(key, str) and isinstance(version, int) and not isinstance(version, bool):
            found.add((key, version))
    return found


__all__ = ("referenced_versions",)
