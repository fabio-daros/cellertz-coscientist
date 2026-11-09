"""Pluggable scientific tool execution.

The runtime only runs a structured argv. It does not parse
scientific artifacts or rank candidates.

Hermes/OpenClaw can implement this contract later. They are
not reduced to a remote subprocess by this interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Protocol


@dataclass(frozen=True)
class ExecutionRequest:
    argv: tuple[str, ...]
    cwd: Path | None = None
    env: Mapping[str, str] | None = None
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class ExecutionResult:
    status: Literal["complete", "failed"]
    return_code: int | None
    stdout: str
    stderr: str
    runtime_name: str
    runtime_version: str
    started_at: str
    completed_at: str
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "return_code": self.return_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "runtime_name": self.runtime_name,
            "runtime_version": self.runtime_version,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }


class ExecutionRuntime(Protocol):
    name: str
    version: str

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        ...
