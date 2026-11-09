"""Operational protein-run state, independent of the Supervisor process.

The MCP server reads this store. The Supervisor may write it. Neither
side imports the other: the JSON document is the boundary, so a later
S3/DynamoDB/Postgres implementation can replace the file store without
changing the MCP tool.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

SCHEMA_VERSION = "1"
STAGE_NAMES = (
    "generation",
    "scoring",
    "ranking",
    "critique",
    "prioritization",
)
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def default_run_state_root() -> Path:
    return Path(os.environ.get("CELLERTZ_RUN_STATE_ROOT", "outputs/runs"))


def is_safe_run_id(run_id: str) -> bool:
    return bool(run_id) and _RUN_ID_RE.fullmatch(run_id) is not None


@dataclass(frozen=True)
class ProteinRunStageState:
    status: str
    missing_ipsae: int | None = None
    error: str | None = None
    grounded_context: dict[str, Any] | None = None
    explanation: dict[str, Any] | None = None

    def to_operational_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": self.status}
        if self.missing_ipsae is not None:
            payload["missing_ipsae"] = self.missing_ipsae
        if self.error is not None:
            payload["error"] = self.error
        if self.grounded_context is not None:
            payload["grounded_context"] = dict(self.grounded_context)
        if self.explanation is not None:
            payload["explanation"] = dict(self.explanation)
        return payload


@dataclass(frozen=True)
class ProteinRunState:
    run_id: str
    found: bool
    schema_version: str = SCHEMA_VERSION
    generation: ProteinRunStageState | None = None
    scoring: ProteinRunStageState | None = None
    ranking: ProteinRunStageState | None = None
    critique: ProteinRunStageState | None = None
    prioritization: ProteinRunStageState | None = None
    error: str | None = None

    def to_tool_dict(self) -> dict[str, Any]:
        if not self.found:
            payload: dict[str, Any] = {
                "found": False,
                "run_id": self.run_id,
            }
            if self.error is not None:
                payload["error"] = self.error
            return payload
        result: dict[str, Any] = {
            "found": True,
            "run_id": self.run_id,
        }
        for name in STAGE_NAMES:
            stage = getattr(self, name)
            if stage is not None:
                result[name] = stage.to_operational_dict()
        return result

    def to_document(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
        }
        for name in STAGE_NAMES:
            stage = getattr(self, name)
            if stage is not None:
                document[name] = stage.to_operational_dict()
        return document


class ProteinRunStateReader(Protocol):
    def get_run_state(self, run_id: str) -> ProteinRunState:
        ...


class ProteinRunStateWriter(Protocol):
    def put_run_state(self, state: ProteinRunState) -> None:
        ...


def canonical_run_state_json(document: dict[str, Any]) -> str:
    return json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ) + "\n"


def stage_from_mapping(payload: object) -> ProteinRunStageState | None:
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    if not isinstance(status, str) or not status:
        return None
    missing = payload.get("missing_ipsae")
    error = payload.get("error")
    grounded = payload.get("grounded_context")
    explanation = payload.get("explanation")
    return ProteinRunStageState(
        status=status,
        missing_ipsae=missing if isinstance(missing, int) else None,
        error=error if isinstance(error, str) else None,
        grounded_context=grounded if isinstance(grounded, dict) else None,
        explanation=explanation if isinstance(explanation, dict) else None,
    )


def state_from_document(run_id: str, document: dict[str, Any]) -> ProteinRunState:
    stages = {
        name: stage_from_mapping(document.get(name)) for name in STAGE_NAMES
    }
    if any(stage is None for stage in stages.values()):
        return ProteinRunState(
            run_id=run_id,
            found=False,
            error="invalid_state",
        )
    schema_version = document.get("schema_version", SCHEMA_VERSION)
    if not isinstance(schema_version, str):
        schema_version = SCHEMA_VERSION
    return ProteinRunState(
        run_id=str(document.get("run_id") or run_id),
        found=True,
        schema_version=schema_version,
        **stages,
    )


class FileProteinRunStateStore:
    """JSON documents under ``{root}/{run_id}/state.json``."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_run_state_root()

    def state_path(self, run_id: str) -> Path | None:
        if not is_safe_run_id(run_id):
            return None
        return self.root / run_id / "state.json"

    def get_run_state(self, run_id: str) -> ProteinRunState:
        path = self.state_path(run_id)
        if path is None:
            return ProteinRunState(run_id=run_id, found=False, error="invalid_run_id")
        if not path.is_file():
            return ProteinRunState(run_id=run_id, found=False)
        try:
            document = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return ProteinRunState(
                run_id=run_id,
                found=False,
                error="invalid_state",
            )
        if not isinstance(document, dict):
            return ProteinRunState(
                run_id=run_id,
                found=False,
                error="invalid_state",
            )
        return state_from_document(run_id, document)

    def put_run_state(self, state: ProteinRunState) -> None:
        if not state.found:
            raise ValueError("cannot persist a missing run")
        path = self.state_path(state.run_id)
        if path is None:
            raise ValueError("invalid run_id")
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = canonical_run_state_json(state.to_document())
        fd, tmp_name = tempfile.mkstemp(
            prefix="state.",
            suffix=".json.tmp",
            dir=path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
