"""Backend selection for episodic ScientificRunMemoryStore.

File is the default. Databricks is explicit and imported lazily.
Domain models do not depend on this module.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from cellertz.second_brain.episodic.models import ScientificRunMemoryError
from cellertz.second_brain.episodic.store import (
    FileScientificRunMemoryStore,
    ScientificRunMemoryStore,
)

BACKEND_FILE = "file"
BACKEND_DATABRICKS = "databricks"
ALLOWED_BACKENDS = frozenset({BACKEND_FILE, BACKEND_DATABRICKS})
SCIENTIFIC_MEMORY_BACKEND_ENV = "CELLERTZ_SCIENTIFIC_MEMORY_BACKEND"


def resolve_scientific_memory_backend(
    backend: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    if backend is not None and str(backend).strip():
        value = str(backend).strip().lower()
    else:
        env = os.environ if environ is None else environ
        value = env.get(SCIENTIFIC_MEMORY_BACKEND_ENV, "").strip().lower()
        if not value:
            value = BACKEND_FILE
    if value not in ALLOWED_BACKENDS:
        raise ScientificRunMemoryError(
            f"unknown_scientific_memory_backend:{value}"
        )
    return value


def create_scientific_run_memory_store(
    *,
    backend: str | None = None,
    file_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
    executor: Any = None,
    config: Any = None,
) -> ScientificRunMemoryStore:
    resolved = resolve_scientific_memory_backend(backend, environ)
    if resolved == BACKEND_FILE:
        return FileScientificRunMemoryStore(root=file_root)
    return _create_databricks_store(
        environ=environ,
        executor=executor,
        config=config,
    )


def _create_databricks_store(
    *,
    environ: Mapping[str, str] | None,
    executor: Any,
    config: Any,
) -> ScientificRunMemoryStore:
    from cellertz.second_brain.episodic.databricks.executor import (
        connect_databricks_sql_executor,
    )
    from cellertz.second_brain.episodic.databricks.store import (
        DatabricksMemoryConfig,
        DatabricksScientificRunMemoryStore,
    )

    resolved_config = config or DatabricksMemoryConfig.from_environ(environ)
    resolved_executor = executor
    if resolved_executor is None:
        resolved_executor = connect_databricks_sql_executor(resolved_config)
    return DatabricksScientificRunMemoryStore(
        config=resolved_config,
        executor=resolved_executor,
    )
