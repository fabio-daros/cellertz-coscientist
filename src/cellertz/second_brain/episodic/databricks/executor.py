"""Databricks SQL execution port for episodic scientific run memory.

Production uses databricks-sql-connector behind this protocol.
Tests use InMemoryDatabricksSqlExecutor. No Spark, JVM, or network
in domain tests.

SQL uses native named markers (:run_id), not legacy pyformat.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from cellertz.second_brain.episodic.models import ScientificRunMemoryError

DATABRICKS_OPTIONAL_INSTALL = (
    "databricks_sql_optional_dependency_missing:"
    " pip install 'cellertz-ai-coscientist[databricks]'"
)
CONCURRENT_WRITE_MARKERS = (
    "concurrentappendexception",
    "concurrentdeletereadexception",
    "concurrentdeletedeleteexception",
    "concurrenttransactionexception",
    "metadatachangedexception",
    "delta_concurrent",
    "concurrent append",
    "concurrent modification",
    "concurrent update",
    "conflicting commit",
)


class DatabricksConcurrentWriteError(ScientificRunMemoryError):
    """Recognized Delta concurrent-write conflict. Not an overwrite signal."""


@dataclass(frozen=True)
class SqlResult:
    rows: tuple[dict[str, Any], ...] = ()
    rowcount: int = 0


class DatabricksSqlExecutor(Protocol):
    def execute(
        self,
        statement: str,
        parameters: Mapping[str, object] | None = None,
    ) -> SqlResult: ...


@dataclass
class InMemoryDatabricksSqlExecutor:
    """Deterministic fake. Models row cardinality, not unique-key lookup."""

    rows: list[dict[str, object]] = field(default_factory=list)
    statements: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    schema_ensured: bool = False
    concurrent_merge_failures: int = 0
    unrecognized_merge_error: str | None = None

    def execute(
        self,
        statement: str,
        parameters: Mapping[str, object] | None = None,
    ) -> SqlResult:
        params = dict(parameters or {})
        self.statements.append((statement, params))
        kind = _statement_kind(statement)
        if kind == "create":
            self.schema_ensured = True
            return SqlResult()
        if kind == "alter":
            return SqlResult()
        if kind == "merge":
            return self._merge(params)
        if kind == "select":
            return self._select(params)
        raise ScientificRunMemoryError("unsupported_sql")

    def rows_for(self, run_id: str) -> list[dict[str, object]]:
        return [row for row in self.rows if row.get("run_id") == run_id]

    def duplicate_run_row(self, run_id: str) -> None:
        matches = self.rows_for(run_id)
        if not matches:
            raise ScientificRunMemoryError("scientific_memory_missing")
        self.rows.append(dict(matches[0]))

    def _merge(self, parameters: dict[str, object]) -> SqlResult:
        if self.unrecognized_merge_error is not None:
            raise ScientificRunMemoryError(self.unrecognized_merge_error)
        if self.concurrent_merge_failures > 0:
            self.concurrent_merge_failures -= 1
            raise DatabricksConcurrentWriteError("databricks_concurrent_write")
        run_id = str(parameters["run_id"])
        if self.rows_for(run_id):
            return SqlResult(rowcount=0)
        self.rows.append(dict(parameters))
        return SqlResult(rowcount=1)

    def _select(self, parameters: dict[str, object]) -> SqlResult:
        values = list(self.rows)
        run_id = parameters.get("run_id")
        if run_id is not None:
            values = [row for row in values if row.get("run_id") == run_id]
        target_id = parameters.get("target_id")
        if target_id is not None:
            values = [row for row in values if row.get("target_id") == target_id]
        values.sort(key=lambda row: str(row.get("run_id") or ""))
        return SqlResult(
            rows=tuple(dict(row) for row in values),
            rowcount=len(values),
        )


class DatabricksConnectorExecutor:
    """Production executor. Imports databricks-sql-connector lazily."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(
        self,
        statement: str,
        parameters: Mapping[str, object] | None = None,
    ) -> SqlResult:
        cursor = None
        try:
            cursor = self._connection.cursor()
            if parameters:
                cursor.execute(statement, dict(parameters))
            else:
                cursor.execute(statement)
            if not cursor.description:
                return SqlResult(rowcount=int(cursor.rowcount or 0))
            columns = [item[0] for item in cursor.description]
            rows = tuple(
                dict(zip(columns, raw, strict=False)) for raw in cursor.fetchall()
            )
            return SqlResult(rows=rows, rowcount=len(rows))
        except DatabricksConcurrentWriteError:
            raise
        except Exception as exc:
            if is_concurrent_write_error(exc):
                raise DatabricksConcurrentWriteError(
                    "databricks_concurrent_write"
                ) from exc
            raise ScientificRunMemoryError("databricks_sql_failed") from exc
        finally:
            if cursor is not None:
                cursor.close()


def connect_databricks_sql_executor(config: Any) -> DatabricksConnectorExecutor:
    try:
        sql = _import_databricks_sql()
    except ImportError as exc:
        raise ScientificRunMemoryError(DATABRICKS_OPTIONAL_INSTALL) from exc
    try:
        connection = sql.connect(
            server_hostname=config.server_hostname,
            http_path=config.http_path,
            access_token=config.access_token,
        )
    except Exception as exc:
        raise ScientificRunMemoryError("databricks_sql_connect_failed") from exc
    return DatabricksConnectorExecutor(connection)


def is_concurrent_write_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    name = type(exc).__name__.lower()
    haystack = f"{name} {text}"
    return any(marker in haystack for marker in CONCURRENT_WRITE_MARKERS)


def _import_databricks_sql() -> Any:
    from databricks import sql

    return sql


def _statement_kind(statement: str) -> str:
    head = " ".join(statement.strip().split()).upper()
    if head.startswith("CREATE TABLE"):
        return "create"
    if head.startswith("ALTER TABLE"):
        return "alter"
    if head.startswith("MERGE"):
        return "merge"
    if head.startswith("SELECT"):
        return "select"
    return "other"
