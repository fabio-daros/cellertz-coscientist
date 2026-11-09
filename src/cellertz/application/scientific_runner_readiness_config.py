"""Server-owned scientific-runner readiness configuration.

Parsing this configuration does not start a probe, a runner, a
process, or a server. The browser cannot supply these values.
There is no probe executable setting.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

BACKEND_ENV = "CELLERTZ_SCIENTIFIC_RUNNER_READINESS"
MANIFEST_ENV = "CELLERTZ_SCIENTIFIC_RUNNER_MANIFEST"
DISABLED = "disabled"
MANIFEST = "manifest"
_BACKENDS = frozenset({DISABLED, MANIFEST})


class ScientificRunnerConfigurationError(Exception):
    """The process is not allowed to select this runner configuration."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ScientificRunnerSettings:
    backend: str
    manifest_path: str | None


def resolve_scientific_runner_settings(
    environ: Mapping[str, str] | None = None,
) -> ScientificRunnerSettings:
    env = os.environ if environ is None else environ
    backend = env.get(BACKEND_ENV, DISABLED).strip().lower()
    if backend not in _BACKENDS:
        raise ScientificRunnerConfigurationError("invalid_scientific_runner_readiness")
    if backend == DISABLED:
        return ScientificRunnerSettings(DISABLED, None)
    path = _absolute_path(env.get(MANIFEST_ENV, ""), "scientific_runner_manifest_required")
    return ScientificRunnerSettings(MANIFEST, path)


def _absolute_path(raw: str, missing_code: str) -> str:
    text = raw.strip()
    if not text:
        raise ScientificRunnerConfigurationError(missing_code)
    if (
        "\x00" in text
        or "\n" in text
        or "\r" in text
        or len(text) > 4096
        or not Path(text).is_absolute()
    ):
        raise ScientificRunnerConfigurationError("invalid_scientific_runner_manifest")
    return text
