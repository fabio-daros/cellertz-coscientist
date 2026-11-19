"""Read-only MCP adapter over the verified attempt catalog.

Numbers come from attempt_evidence, never from this module. Tool names
match the attempt coordinator so both surfaces share one vocabulary.
This module must not import the Protein Supervisor.
"""

from __future__ import annotations

from typing import Any

from cellertz.application.attempt_evidence import (
    compare_metrics,
    evidence_payload,
    list_attempts,
    load_catalog,
    merge_demonstration_attempts,
    resolve_attempt,
)

_catalog: dict[str, dict[str, Any]] | None = None


def get_attempt_catalog() -> dict[str, dict[str, Any]]:
    global _catalog
    if _catalog is None:
        _catalog = load_catalog()
    merge_demonstration_attempts(_catalog)
    return _catalog


def set_attempt_catalog(catalog: dict[str, dict[str, Any]] | None) -> None:
    global _catalog
    _catalog = catalog


def list_verified_attempts() -> dict[str, Any]:
    """List verified attempts. Does not rank them. Read-only."""
    return list_attempts(get_attempt_catalog())


def get_attempt_evidence(attempt_id: str) -> dict[str, Any]:
    """Return the verified selection rule and provenance. Read-only."""
    return _resolved(attempt_id, lambda chosen, item: evidence_payload(chosen, item))


def compare_model_metrics(attempt_id: str, metrics: object = None) -> dict[str, Any]:
    """Return recorded metric cells. Does not score them. Read-only."""
    return _resolved(attempt_id, lambda _chosen, item: compare_metrics(item, metrics))


def _resolved(attempt_id: str, read: Any) -> dict[str, Any]:
    resolved = resolve_attempt(get_attempt_catalog(), attempt_id, None)
    if resolved["status"] != "ok":
        return {key: value for key, value in resolved.items() if key != "item"}
    chosen = str(resolved["resolved_attempt_id"])
    payload = read(chosen, resolved["item"])
    payload["requested_id"] = resolved["requested_id"]
    payload["resolved_attempt_id"] = chosen
    payload.setdefault("status", "ok")
    return payload
