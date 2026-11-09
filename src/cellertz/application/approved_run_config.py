"""Server-owned selection of the approved-run store.

Parsing this configuration does not create a database and does not
start a server. The scientific-memory backend is a different setting.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from pathlib import Path

from cellertz.application.approved_run import ApprovedRunStore, InMemoryApprovedRunStore
from cellertz.application.approved_run_store import SQLiteApprovedRunStore

STORE_ENV = "CELLERTZ_APPROVED_RUN_STORE"
DB_ENV = "CELLERTZ_APPROVED_RUN_DB"
WORKER_POLL_ENV = "CELLERTZ_APPROVED_RUN_WORKER_POLL_SECONDS"
MEMORY = "memory"
SQLITE = "sqlite"
_BACKENDS = frozenset({MEMORY, SQLITE})


class ApprovedRunConfigurationError(Exception):
    """The process is not allowed to choose a store."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def resolve_approved_run_backend(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str | None]:
    env = os.environ if environ is None else environ
    backend = env.get(STORE_ENV, MEMORY).strip().lower()
    if backend not in _BACKENDS:
        raise ApprovedRunConfigurationError("invalid_approved_run_store")
    if backend == MEMORY:
        return MEMORY, None
    path = env.get(DB_ENV, "").strip()
    if not path:
        raise ApprovedRunConfigurationError("approved_run_db_required")
    return SQLITE, path


def open_approved_run_store(environ: Mapping[str, str] | None = None) -> ApprovedRunStore:
    backend, path = resolve_approved_run_backend(environ)
    if backend == MEMORY:
        return InMemoryApprovedRunStore()
    assert path is not None
    return SQLiteApprovedRunStore(Path(path))


def resolve_approved_run_worker_poll_seconds(environ: Mapping[str, str] | None = None) -> float:
    env = os.environ if environ is None else environ
    raw = env.get(WORKER_POLL_ENV, "0.25").strip()
    try:
        value = float(raw)
    except ValueError:
        raise ApprovedRunConfigurationError("invalid_approved_run_worker_poll_seconds") from None
    if not math.isfinite(value) or value <= 0 or value > 60:
        raise ApprovedRunConfigurationError("invalid_approved_run_worker_poll_seconds")
    return value
