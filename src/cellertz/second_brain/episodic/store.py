"""Episodic scientific run memory store contract and local file adapter.

Domain code depends on ScientificRunMemoryStore, not on this file
adapter. DatabricksScientificRunMemoryStore implements the same
protocol without changing projectors or the Supervisor.

Queries return observed episodic records. They do not mean that
Cellertz has validated a scientific claim.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol

from cellertz.second_brain.episodic.models import (
    CandidateMemoryHit,
    ScientificRunMemory,
    ScientificRunMemoryConflict,
    ScientificRunMemoryError,
    scientific_run_memory_from_document,
    validate_scientific_run_memory,
)
from cellertz.state.run_state import is_safe_run_id

MEMORY_FILENAME = "memory.json"
SCIENTIFIC_MEMORY_ENV = "CELLERTZ_SCIENTIFIC_MEMORY_ROOT"
DEFAULT_SCIENTIFIC_MEMORY_ROOT = Path("outputs/scientific_memory/episodic")


def default_scientific_memory_root() -> Path:
    return Path(
        os.environ.get(
            SCIENTIFIC_MEMORY_ENV,
            str(DEFAULT_SCIENTIFIC_MEMORY_ROOT),
        )
    )


def canonical_scientific_memory_json(document: dict[str, object]) -> str:
    return (
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    )


class ScientificRunMemoryReader(Protocol):
    def get_run(self, run_id: str) -> ScientificRunMemory | None: ...

    def find_runs_by_target(
        self,
        target_id: str,
    ) -> tuple[ScientificRunMemory, ...]: ...

    def find_candidate(
        self,
        candidate_id: str,
    ) -> tuple[CandidateMemoryHit, ...]: ...


class ScientificRunMemoryWriter(Protocol):
    def put(self, memory: ScientificRunMemory) -> ScientificRunMemory: ...


class ScientificRunMemoryStore(
    ScientificRunMemoryReader,
    ScientificRunMemoryWriter,
    Protocol,
):
    """Append-safe episodic scientific run memory persistence."""


class FileScientificRunMemoryStore:
    """JSON documents under ``{root}/runs/{run_id}/memory.json``."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (
            Path(root) if root is not None else default_scientific_memory_root()
        )

    def memory_path(self, run_id: str) -> Path | None:
        if not is_safe_run_id(run_id):
            return None
        return self.root / "runs" / run_id / MEMORY_FILENAME

    def get_run(self, run_id: str) -> ScientificRunMemory | None:
        path = self.memory_path(run_id)
        if path is None or not path.is_file():
            return None
        return self._load_path(path)

    def find_runs_by_target(
        self,
        target_id: str,
    ) -> tuple[ScientificRunMemory, ...]:
        matches = [
            memory
            for memory in self._iter_memories()
            if memory.target_id == target_id
        ]
        return tuple(sorted(matches, key=lambda item: item.run_id))

    def find_candidate(
        self,
        candidate_id: str,
    ) -> tuple[CandidateMemoryHit, ...]:
        hits: list[CandidateMemoryHit] = []
        for memory in self._iter_memories():
            for candidate in memory.candidates:
                if candidate.candidate_id == candidate_id:
                    hits.append(
                        CandidateMemoryHit(
                            run_id=memory.run_id,
                            candidate=candidate,
                        )
                    )
        return tuple(sorted(hits, key=lambda item: item.run_id))

    def put(self, memory: ScientificRunMemory) -> ScientificRunMemory:
        validate_scientific_run_memory(memory)
        path = self.memory_path(memory.run_id)
        if path is None:
            raise ScientificRunMemoryError("invalid_run_id")
        if path.is_file():
            existing = self._load_path(path)
            if existing.memory_digest == memory.memory_digest:
                return existing
            raise ScientificRunMemoryConflict(
                "scientific_memory_conflict:"
                f"{memory.run_id}:{existing.memory_digest}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(path, memory.to_document())
        return memory

    def _iter_memories(self) -> tuple[ScientificRunMemory, ...]:
        runs_root = self.root / "runs"
        if not runs_root.is_dir():
            return ()
        memories: list[ScientificRunMemory] = []
        for child in sorted(runs_root.iterdir()):
            path = child / MEMORY_FILENAME
            if not path.is_file():
                continue
            memories.append(self._load_path(path))
        return tuple(memories)

    def _load_path(self, path: Path) -> ScientificRunMemory:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ScientificRunMemoryError("scientific_memory_unreadable") from exc
        if not isinstance(document, dict):
            raise ScientificRunMemoryError("scientific_memory_malformed")
        try:
            return scientific_run_memory_from_document(document)
        except ScientificRunMemoryError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ScientificRunMemoryError("scientific_memory_malformed") from exc


def _write_json_atomic(path: Path, document: dict[str, object]) -> None:
    encoded = canonical_scientific_memory_json(document)
    fd, tmp_name = tempfile.mkstemp(
        prefix="memory.",
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
