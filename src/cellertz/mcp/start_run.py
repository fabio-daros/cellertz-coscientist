"""Thin MCP adapter for fixture-only protein-run starts.

Validates authorization and input, then forwards to ProteinRunStarter.
This module must not import the Supervisor or implement scoring.
"""

from __future__ import annotations

from typing import Any, Protocol

from cellertz.runs.authorization import reject_requested_generator
from cellertz.runs.models import ProteinFixtureRunRequest, ProteinRunStartResult


class ProteinRunStarter(Protocol):
    def start_fixture_run(
        self,
        request: ProteinFixtureRunRequest,
        idempotency_key: str,
    ) -> ProteinRunStartResult:
        ...


_starter: ProteinRunStarter | None = None


def get_run_starter() -> ProteinRunStarter:
    global _starter
    if _starter is None:
        from cellertz.runs.starter import LocalProteinRunStarter

        _starter = LocalProteinRunStarter()
    return _starter


def set_run_starter(starter: ProteinRunStarter | None) -> None:
    global _starter
    _starter = starter


def protein_start_fixture_run(
    target_id: str,
    objective: str,
    n_candidates: int,
    idempotency_key: str,
    generator: str | None = None,
) -> dict[str, Any]:
    """Start one fixture-only protein-design run.

    Requires CELLERTZ_MCP_MUTATION_MODE=fixture_only. The generator is
    forced to fixture; BindCraft, RFdiffusion, and mBER are rejected.
    """
    forbidden = reject_requested_generator(generator)
    if forbidden is not None:
        return {
            "error": forbidden,
            "created": False,
            "idempotent_replay": False,
            "status": "rejected",
            "mode": "fixture",
            "idempotency_key": idempotency_key,
        }
    request = ProteinFixtureRunRequest(
        target_id=target_id,
        objective=objective,
        n_candidates=n_candidates,
    )
    return get_run_starter().start_fixture_run(request, idempotency_key).to_dict()
