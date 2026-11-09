"""Server-owned scientific-authorization store selection.

Parsing this configuration does not create a database, authorize a
run, or start a server. The browser cannot supply these values.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from cellertz.application.scientific_authorization import (
    InMemoryScientificAuthorizationStore,
    ScientificAuthorizationStore,
)
from cellertz.application.scientific_authorization_store import SQLiteScientificAuthorizationStore

STORE_ENV = "CELLERTZ_SCIENTIFIC_AUTHORIZATION_STORE"
DB_ENV = "CELLERTZ_SCIENTIFIC_AUTHORIZATION_DB"
MEMORY = "memory"
SQLITE = "sqlite"
_BACKENDS = frozenset({MEMORY, SQLITE})


class ScientificAuthorizationConfigurationError(Exception):
    """The process is not allowed to choose this authorization store."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def resolve_scientific_authorization_backend(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str | None]:
    env = os.environ if environ is None else environ
    backend = env.get(STORE_ENV, MEMORY).strip().lower()
    if backend not in _BACKENDS:
        raise ScientificAuthorizationConfigurationError("invalid_scientific_authorization_store")
    if backend == MEMORY:
        return MEMORY, None
    path = env.get(DB_ENV, "").strip()
    if not path or "\x00" in path or "\n" in path or "\r" in path:
        raise ScientificAuthorizationConfigurationError("scientific_authorization_db_required")
    return SQLITE, path


def open_scientific_authorization_store(
    environ: Mapping[str, str] | None = None,
) -> ScientificAuthorizationStore:
    backend, path = resolve_scientific_authorization_backend(environ)
    if backend == MEMORY:
        return InMemoryScientificAuthorizationStore()
    assert path is not None
    return SQLiteScientificAuthorizationStore(Path(path))
