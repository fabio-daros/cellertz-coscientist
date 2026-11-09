"""Developer-only ingest of episodic scientific run memory.

Projects an observed ScientificRunMemory from explicit completed-run
artifacts. Default is inspect-only. Persist requires --persist.
Backend is file by default. Databricks requires explicit selection.
Does not promote KnowledgePackages. Not an MCP tool.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from cellertz.second_brain.episodic.factory import (
    BACKEND_FILE,
    create_scientific_run_memory_store,
    resolve_scientific_memory_backend,
)
from cellertz.second_brain.episodic.models import (
    ScientificRunMemoryError,
    ScientificRunMemoryProjectionError,
)
from cellertz.second_brain.episodic.protein_design import (
    project_protein_design_run,
)
from cellertz.second_brain.episodic.store import (
    FileScientificRunMemoryStore,
    ScientificRunMemoryStore,
)

StoreFactory = Callable[..., ScientificRunMemoryStore]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Project episodic scientific run memory from a completed "
            "protein-design run. Inspect-only unless --persist is set. "
            "Does not promote semantic KnowledgePackages."
        )
    )
    parser.add_argument(
        "--run-artifact-dir",
        required=True,
        type=Path,
        help="Supervisor artifact directory containing run.json and stage JSON.",
    )
    parser.add_argument(
        "--execution-output-dir",
        required=True,
        type=Path,
        help="Execution output directory containing the scientific handoff.",
    )
    parser.add_argument(
        "--memory-root",
        type=Path,
        default=None,
        help="File store root. Used only with --persist --backend file.",
    )
    parser.add_argument(
        "--backend",
        choices=("file", "databricks"),
        default=None,
        help="Episodic store backend. Overrides CELLERTZ_SCIENTIFIC_MEMORY_BACKEND.",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Write projected observed episodic memory to the selected store.",
    )
    return parser.parse_args(argv)


def ingest_protein_run(
    args: argparse.Namespace,
    *,
    store: ScientificRunMemoryStore | None = None,
    store_factory: StoreFactory | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    try:
        memory = project_protein_design_run(
            run_artifact_dir=args.run_artifact_dir,
            execution_output_dir=args.execution_output_dir,
        )
        backend = resolve_scientific_memory_backend(args.backend, environ)
    except ScientificRunMemoryProjectionError as exc:
        return _failed(str(exc), persist_attempted=False)
    except ScientificRunMemoryError as exc:
        return _failed(str(exc), persist_attempted=False)
    report: dict[str, Any] = {
        "ok": True,
        "status": "inspect_only",
        "persist_attempted": False,
        "backend": backend,
        "memory_digest": memory.memory_digest,
        "memory_house": memory.memory_house,
        "epistemic_status": memory.epistemic_status,
        "run_id": memory.run_id,
        "execution_id": memory.execution_id,
        "target_id": memory.target_id,
        "memory": memory.to_document(),
    }
    if not args.persist:
        return report
    report["persist_attempted"] = True
    try:
        resolved_store = _open_store(
            args,
            backend=backend,
            store=store,
            store_factory=store_factory,
            environ=environ,
        )
        stored = resolved_store.put(memory)
    except ScientificRunMemoryError as exc:
        return _failed(
            str(exc),
            persist_attempted=True,
            backend=backend,
            memory_digest=memory.memory_digest,
            memory_house=memory.memory_house,
            epistemic_status=memory.epistemic_status,
            run_id=memory.run_id,
        )
    report["status"] = "persisted"
    report["memory_digest"] = stored.memory_digest
    report["memory_house"] = stored.memory_house
    report["epistemic_status"] = stored.epistemic_status
    report["memory"] = stored.to_document()
    if isinstance(resolved_store, FileScientificRunMemoryStore):
        path = resolved_store.memory_path(stored.run_id)
        if path is not None:
            report["memory_path"] = str(path)
    return report


def main(argv: list[str] | None = None) -> int:
    report = ingest_protein_run(parse_args(argv))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    print(f"memory_digest={report.get('memory_digest')}")
    return 0 if report.get("ok") is True else 1


def _open_store(
    args: argparse.Namespace,
    *,
    backend: str,
    store: ScientificRunMemoryStore | None,
    store_factory: StoreFactory | None,
    environ: Mapping[str, str] | None,
) -> ScientificRunMemoryStore:
    if store is not None:
        return store
    if store_factory is not None:
        return store_factory(
            backend=backend,
            file_root=args.memory_root,
            environ=environ,
        )
    return create_scientific_run_memory_store(
        backend=backend,
        file_root=args.memory_root,
        environ=environ,
    )


def _failed(error: str, **extra: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "status": "failed",
        "error": error,
        "persist_attempted": False,
        "backend": extra.get("backend", BACKEND_FILE),
        "memory_digest": None,
    }
    payload.update(extra)
    return payload


if __name__ == "__main__":
    sys.exit(main())
