"""Authorization for MCP mutating protein-run tools.

Default is disabled. fixture_only is the only enabled mode in 2B.3.
That mode is a real mutation, not a hidden write behind a false
global flag. Real scientific generators stay closed.
"""

from __future__ import annotations

import os

DISABLED = "disabled"
FIXTURE_ONLY = "fixture_only"
ALLOWED_MODES = frozenset({DISABLED, FIXTURE_ONLY})
FORBIDDEN_GENERATORS = frozenset(
    {
        "bindcraft",
        "rfdiffusion",
        "mber",
        "rfdiff",
        "proteinmpnn",
    }
)


def mutation_mode() -> str:
    raw = os.environ.get("CELLERTZ_MCP_MUTATION_MODE", DISABLED)
    mode = (raw or DISABLED).strip().lower()
    if mode not in ALLOWED_MODES:
        return DISABLED
    return mode


def fixture_mutations_allowed() -> bool:
    return mutation_mode() == FIXTURE_ONLY


def reject_requested_generator(generator: str | None) -> str | None:
    if generator is None or generator.strip() == "":
        return None
    name = generator.strip().lower()
    if name == "fixture":
        return None
    if name in FORBIDDEN_GENERATORS or name != "fixture":
        return "generator_not_allowed"
    return None
