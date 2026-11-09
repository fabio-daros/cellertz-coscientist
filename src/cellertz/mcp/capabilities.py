"""Cellertz capability map.

Mutation is explicit. fixture_only is a real mutating capability.
BindCraft, RFdiffusion, mBER, GPU, and wet lab stay closed.
"""

from __future__ import annotations

from typing import Any

from cellertz.runs.authorization import fixture_mutations_allowed, mutation_mode


def cellertz_capabilities() -> dict[str, Any]:
    mode = mutation_mode()
    enabled = fixture_mutations_allowed()
    return {
        "service": "cellertz-ai-coscientist",
        "protein_design": {
            "generation": True,
            "scoring": True,
            "ranking": True,
            "critic": True,
            "prioritization": True,
        },
        "mutation": {
            "enabled": enabled,
            "mode": mode,
            "real_generators_allowed": False,
        },
        "knowledge": {
            "get": True,
            "neighbors": True,
            "ground_package": True,
            "list": False,
            "semantic_search": False,
            "health": False,
        },
        "episodic_memory": {
            "memory_house": "episodic",
            "epistemic_status": "observed",
            "write": False,
            "get_run": True,
            "find_runs_by_target": True,
            "find_candidate": True,
        },
    }
