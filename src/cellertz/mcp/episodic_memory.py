"""Thin read-only MCP adapter over ScientificRunMemoryReader.

Answers what was recorded in an episodic run. It does not validate
scientific claims, ingest missing runs, or let the caller choose a
backend. File is the default. Databricks is opened only when a read
actually runs and the server is configured for that backend.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cellertz.second_brain.episodic.factory import (
    create_scientific_run_memory_store,
)
from cellertz.second_brain.episodic.models import (
    EPISTEMIC_STATUS_OBSERVED,
    MEMORY_HOUSE_EPISODIC,
    ScientificRunMemory,
    ScientificRunMemoryError,
)
from cellertz.second_brain.episodic.store import ScientificRunMemoryReader
from cellertz.state.run_state import is_safe_run_id

RESPONSE_SCHEMA = "episodic_retrieval_v1"
INTERPRETATION = (
    "Recorded execution observations; not validated scientific knowledge."
)
MAX_LIMIT = 100
_SAFE_ERROR_CODES = frozenset(
    {
        "databricks_config_missing",
        "databricks_sql_connect_failed",
        "databricks_sql_failed",
        "databricks_sql_optional_dependency_missing",
        "invalid_candidate_id",
        "invalid_limit",
        "invalid_run_id",
        "invalid_target_id",
        "scientific_memory_digest_mismatch",
        "scientific_memory_duplicate_run_id",
        "scientific_memory_index_mismatch",
        "scientific_memory_malformed",
        "scientific_memory_unavailable",
        "scientific_memory_unreadable",
        "unknown_scientific_memory_backend",
    }
)

_reader: ScientificRunMemoryReader | None = None


def get_scientific_memory_reader() -> ScientificRunMemoryReader:
    global _reader
    if _reader is None:
        _reader = create_scientific_run_memory_store()
    return _reader


def set_scientific_memory_reader(
    reader: ScientificRunMemoryReader | None,
) -> None:
    global _reader
    _reader = reader


def scientific_memory_get_run(run_id: str) -> dict[str, Any]:
    """Return one recorded ScientificRunMemory. Read-only."""
    echoed = _echo_identifier(run_id)
    if not _valid_text(run_id) or not is_safe_run_id(run_id):
        return _failed("invalid_run_id", run_id=echoed)
    return _guard(_load_run, run_id, run_id=run_id)


def scientific_memory_find_runs_by_target(
    target_id: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Return compact observed runs for one exact target. Read-only."""
    if not _valid_text(target_id):
        return _failed_page(
            "invalid_target_id",
            target_id=_echo_identifier(target_id),
            limit=limit,
        )
    bounded = _valid_limit(limit)
    if bounded is None:
        return _failed_page("invalid_limit", target_id=target_id, limit=limit)
    return _guard(
        _load_target,
        target_id,
        bounded,
        target_id=target_id,
        limit=bounded,
    )


def scientific_memory_find_candidate(
    candidate_id: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Return observed candidate occurrences. Read-only."""
    if not _valid_text(candidate_id):
        return _failed_page(
            "invalid_candidate_id",
            candidate_id=_echo_identifier(candidate_id),
            limit=limit,
        )
    bounded = _valid_limit(limit)
    if bounded is None:
        return _failed_page(
            "invalid_limit",
            candidate_id=candidate_id,
            limit=limit,
        )
    return _guard(
        _load_candidate,
        candidate_id,
        bounded,
        candidate_id=candidate_id,
        limit=bounded,
    )


def _load_run(run_id: str) -> dict[str, Any]:
    memory = get_scientific_memory_reader().get_run(run_id)
    if memory is None:
        return _surface(ok=True, found=False) | {
            "run_id": run_id,
            "memory": None,
            "memory_digest": None,
        }
    return _surface(ok=True, found=True) | {
        "run_id": run_id,
        "memory_digest": memory.memory_digest,
        "memory": memory.to_document(),
    }


def _load_target(target_id: str, limit: int) -> dict[str, Any]:
    memories = get_scientific_memory_reader().find_runs_by_target(target_id)
    ordered = tuple(sorted(memories, key=lambda item: item.run_id))
    return _page(
        "target_id",
        target_id,
        limit,
        ordered,
        lambda memory: _run_summary(memory),
    )


def _load_candidate(candidate_id: str, limit: int) -> dict[str, Any]:
    hits = get_scientific_memory_reader().find_candidate(candidate_id)
    ordered = tuple(sorted(hits, key=lambda item: item.run_id))
    return _page(
        "candidate_id",
        candidate_id,
        limit,
        ordered,
        lambda hit: {
            "run_id": hit.run_id,
            "candidate": hit.candidate.to_document(),
        },
    )


def _run_summary(memory: ScientificRunMemory) -> dict[str, Any]:
    return {
        "run_id": memory.run_id,
        "target_id": memory.target_id,
        "domain": memory.domain,
        "generator": memory.generator,
        "objective": memory.objective,
        "execution_id": memory.execution_id,
        "memory_digest": memory.memory_digest,
        "recorded_at": memory.recorded_at,
        "memory_house": memory.memory_house,
        "epistemic_status": memory.epistemic_status,
        "candidate_count": len(memory.candidates),
    }


def _guard(
    loader: Callable[..., dict[str, Any]],
    *args: object,
    **failure: object,
) -> dict[str, Any]:
    try:
        return loader(*args)
    except ScientificRunMemoryError as exc:
        return _failure(_safe_error_code(exc), failure)
    except Exception:  # noqa: BLE001 — hide backend text at the response boundary
        return _failure("scientific_memory_unavailable", failure)


def _failure(code: str, fields: dict[str, object]) -> dict[str, Any]:
    if "run_id" in fields:
        return _failed(code, **fields)
    return _failed_page(code, **fields)


def _failed(code: str, **fields: object) -> dict[str, Any]:
    payload = _surface(ok=False, found=False)
    payload["error"] = _public_code(code)
    payload.update(fields)
    payload["memory"] = None
    payload["memory_digest"] = None
    return payload


def _failed_page(code: str, **fields: object) -> dict[str, Any]:
    payload = _surface(ok=False, found=False)
    payload["error"] = _public_code(code)
    payload.update(fields)
    payload["items"] = []
    payload["returned_count"] = 0
    payload["total_count"] = 0
    payload["truncated"] = False
    return payload


def _page(
    key: str,
    identifier: str,
    limit: int,
    ordered: tuple[Any, ...],
    project: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    returned = [project(item) for item in ordered[:limit]]
    return _surface(ok=True, found=len(ordered) > 0) | {
        key: identifier,
        "items": returned,
        "returned_count": len(returned),
        "total_count": len(ordered),
        "limit": limit,
        "truncated": len(ordered) > len(returned),
    }


def _surface(*, ok: bool, found: bool) -> dict[str, Any]:
    return {
        "ok": ok,
        "found": found,
        "response_schema": RESPONSE_SCHEMA,
        "memory_house": MEMORY_HOUSE_EPISODIC,
        "epistemic_status": EPISTEMIC_STATUS_OBSERVED,
        "interpretation": INTERPRETATION,
    }


def _valid_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _echo_identifier(value: object) -> object:
    if isinstance(value, str):
        return value
    return None


def _valid_limit(limit: object) -> int | None:
    if isinstance(limit, bool) or type(limit) is not int:
        return None
    if limit < 1 or limit > MAX_LIMIT:
        return None
    return limit


def _public_code(code: str) -> str:
    if code in _SAFE_ERROR_CODES:
        return code
    return "scientific_memory_unavailable"


def _safe_error_code(exc: BaseException) -> str:
    head = str(exc).split(":", 1)[0].strip()
    return _public_code(head)
