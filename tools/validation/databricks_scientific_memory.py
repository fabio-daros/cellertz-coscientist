"""Developer-only Databricks episodic scientific-memory operator.

Default is inspect-only. Schema creation and writes require explicit
flags. Not an MCP tool. Never prints secrets.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cellertz.second_brain.episodic.databricks import (
    DatabricksMemoryConfig,
    DatabricksMemoryConfigError,
    DatabricksScientificRunMemoryStore,
    DatabricksSqlExecutor,
)
from cellertz.second_brain.episodic.models import (
    ScientificRunMemoryError,
    scientific_run_memory_from_document,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect or persist episodic ScientificRunMemory on Databricks. "
            "Default is non-mutating."
        )
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate configuration. Does not execute SQL.",
    )
    parser.add_argument(
        "--ensure-schema",
        action="store_true",
        help="Create the Delta table if it does not exist.",
    )
    parser.add_argument(
        "--write-memory",
        type=Path,
        default=None,
        help="Put one ScientificRunMemory JSON document.",
    )
    parser.add_argument(
        "--get-run",
        default=None,
        help="Read one run_id. Does not mutate.",
    )
    return parser.parse_args(argv)


def run_databricks_memory(
    args: argparse.Namespace,
    *,
    config: DatabricksMemoryConfig | None = None,
    executor: DatabricksSqlExecutor | None = None,
    store: DatabricksScientificRunMemoryStore | None = None,
) -> dict[str, Any]:
    mutating = bool(args.ensure_schema) + bool(args.write_memory is not None)
    if mutating > 1:
        return _failed("operator_mutating_conflict", mutation_attempted=False)
    try:
        resolved = config or DatabricksMemoryConfig.from_environ()
    except DatabricksMemoryConfigError as exc:
        return _failed(str(exc), mutation_attempted=False)
    report: dict[str, Any] = {
        "ok": True,
        "status": "inspect_only",
        "mutation_attempted": False,
        "config": _redacted_config(resolved),
    }
    needs_store = bool(
        args.ensure_schema or args.write_memory is not None or args.get_run
    )
    if not needs_store:
        return report
    try:
        resolved_store = store or DatabricksScientificRunMemoryStore(
            config=resolved,
            executor=executor or _connect(resolved),
        )
    except ScientificRunMemoryError as exc:
        return _failed(
            str(exc),
            mutation_attempted=False,
            config=_redacted_config(resolved),
        )
    if args.ensure_schema:
        report["mutation_attempted"] = True
        try:
            resolved_store.ensure_schema()
        except ScientificRunMemoryError as exc:
            return _operator_failure(
                exc,
                mutation_attempted=True,
                config=_redacted_config(resolved),
            )
        report["status"] = "schema_ensured"
        return report
    if args.write_memory is not None:
        report["mutation_attempted"] = True
        try:
            memory = _load_memory(args.write_memory)
            stored = resolved_store.put(memory)
        except ScientificRunMemoryError as exc:
            return _operator_failure(
                exc,
                mutation_attempted=True,
                config=_redacted_config(resolved),
            )
        report["status"] = "persisted"
        report["run_id"] = stored.run_id
        report["memory_digest"] = stored.memory_digest
        report["memory_house"] = stored.memory_house
        report["epistemic_status"] = stored.epistemic_status
        return report
    if args.get_run:
        try:
            memory = resolved_store.get_run(args.get_run)
        except ScientificRunMemoryError as exc:
            return _operator_failure(
                exc,
                mutation_attempted=False,
                config=_redacted_config(resolved),
            )
        report["status"] = "read"
        report["found"] = memory is not None
        if memory is not None:
            report["memory"] = memory.to_document()
            report["memory_digest"] = memory.memory_digest
        return report
    return report


def _connect(config: DatabricksMemoryConfig) -> DatabricksSqlExecutor:
    from cellertz.second_brain.episodic.databricks.executor import (
        connect_databricks_sql_executor,
    )

    return connect_databricks_sql_executor(config)


def _load_memory(path: Path) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise ScientificRunMemoryError("scientific_memory_unreadable") from exc
    if not isinstance(document, dict):
        raise ScientificRunMemoryError("scientific_memory_malformed")
    return scientific_run_memory_from_document(document)


def _operator_failure(exc: BaseException, **extra: object) -> dict[str, Any]:
    if isinstance(exc, ScientificRunMemoryError):
        return _failed(str(exc), **extra)
    return _failed("databricks_operator_failed", **extra)


def _redacted_config(config: DatabricksMemoryConfig) -> dict[str, object]:
    return {
        "server_hostname": config.server_hostname,
        "http_path": config.http_path,
        "catalog": config.catalog,
        "schema_name": config.schema_name,
        "table": config.table,
        "access_token": "***" if config.access_token else None,
    }


def _failed(error: str, **extra: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "status": "failed",
        "error": error,
        "mutation_attempted": False,
    }
    payload.update(extra)
    return payload


def main(argv: list[str] | None = None) -> int:
    report = run_databricks_memory(parse_args(argv))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    sys.exit(main())
