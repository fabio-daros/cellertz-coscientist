"""Server-owned scientific-run store and execution selection.

Parsing this configuration does not create a database, consume an
authorization, or start a server. The browser cannot supply these values.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from pathlib import Path

from cellertz.application.scientific_run import InMemoryScientificRunStore, ScientificRunStore
from cellertz.application.scientific_run_store import SQLiteScientificRunStore

STORE_ENV = "CELLERTZ_SCIENTIFIC_RUN_STORE"
DB_ENV = "CELLERTZ_SCIENTIFIC_RUN_DB"
WORKER_POLL_ENV = "CELLERTZ_SCIENTIFIC_RUN_WORKER_POLL_SECONDS"
EXECUTION_ENV = "CELLERTZ_SCIENTIFIC_RUN_EXECUTION"
MEMORY = "memory"
SQLITE = "sqlite"
DISABLED = "disabled"
ENABLED = "enabled"
GOVERNED_FIXTURE = "governed-fixture"
APPTAINER_DEMONSTRATION = "apptainer-demonstration"
_BACKENDS = frozenset({MEMORY, SQLITE})
_EXECUTION = frozenset({DISABLED, ENABLED, GOVERNED_FIXTURE, APPTAINER_DEMONSTRATION})


class ScientificRunConfigurationError(Exception):
    """The process is not allowed to choose this scientific-run configuration."""

    def __init__(self, code: str, missing: tuple[str, ...] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.missing = missing if type(missing) is tuple else ()


def resolve_scientific_run_backend(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str | None]:
    env = os.environ if environ is None else environ
    backend = env.get(STORE_ENV, MEMORY).strip().lower()
    if backend not in _BACKENDS:
        raise ScientificRunConfigurationError("invalid_scientific_run_store")
    if backend == MEMORY:
        return MEMORY, None
    path = env.get(DB_ENV, "").strip()
    if not path or "\x00" in path or "\n" in path or "\r" in path:
        raise ScientificRunConfigurationError("scientific_run_db_required")
    return SQLITE, path


def resolve_scientific_run_worker_poll_seconds(
    environ: Mapping[str, str] | None = None,
) -> float:
    env = os.environ if environ is None else environ
    raw = env.get(WORKER_POLL_ENV, "0.25").strip()
    try:
        value = float(raw)
    except ValueError:
        raise ScientificRunConfigurationError(
            "invalid_scientific_run_worker_poll_seconds"
        ) from None
    if not math.isfinite(value) or value <= 0 or value > 60:
        raise ScientificRunConfigurationError("invalid_scientific_run_worker_poll_seconds")
    return value


def resolve_scientific_run_execution(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    value = env.get(EXECUTION_ENV, DISABLED).strip().lower()
    if value not in _EXECUTION:
        raise ScientificRunConfigurationError("invalid_scientific_run_execution")
    return value


def open_scientific_run_store(environ: Mapping[str, str] | None = None) -> ScientificRunStore:
    backend, path = resolve_scientific_run_backend(environ)
    if backend == MEMORY:
        return InMemoryScientificRunStore()
    assert path is not None
    return SQLiteScientificRunStore(Path(path))
