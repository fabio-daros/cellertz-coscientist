"""Databricks-backed episodic ScientificRunMemoryStore.

Storage only. Does not execute science, promote knowledge, or store
PDB/PAE artifacts. Delta is not a scientific authority: persisted
JSON is revalidated through scientific_run_memory_from_document.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cellertz.second_brain.episodic.databricks.executor import (
    DatabricksConcurrentWriteError,
    DatabricksSqlExecutor,
    connect_databricks_sql_executor,
)
from cellertz.second_brain.episodic.models import (
    CandidateMemoryHit,
    ScientificRunMemory,
    ScientificRunMemoryConflict,
    ScientificRunMemoryError,
    scientific_run_memory_from_document,
    validate_scientific_run_memory,
)
from cellertz.state.run_state import is_safe_run_id

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_TABLE = "scientific_run_memory"
ENV_SERVER_HOSTNAME = "CELLERTZ_DATABRICKS_SERVER_HOSTNAME"
ENV_HTTP_PATH = "CELLERTZ_DATABRICKS_HTTP_PATH"
ENV_ACCESS_TOKEN = "CELLERTZ_DATABRICKS_ACCESS_TOKEN"
ENV_CATALOG = "CELLERTZ_DATABRICKS_CATALOG"
ENV_SCHEMA = "CELLERTZ_DATABRICKS_SCHEMA"
ENV_TABLE = "CELLERTZ_DATABRICKS_TABLE"
MAX_MERGE_ATTEMPTS = 2
DUPLICATE_RUN_ERROR = "scientific_memory_duplicate_run_id"
INDEX_MISMATCH_ERROR = "scientific_memory_index_mismatch"
READ_COLUMNS = "run_id, memory_digest, target_id, memory_document_json"


class DatabricksMemoryConfigError(ScientificRunMemoryError):
    """Invalid Databricks memory configuration."""


@dataclass(frozen=True)
class DatabricksMemoryConfig:
    server_hostname: str
    http_path: str
    catalog: str
    schema_name: str
    table: str = DEFAULT_TABLE
    access_token: str | None = None

    def __repr__(self) -> str:
        token = "***" if self.access_token else None
        return (
            "DatabricksMemoryConfig("
            f"server_hostname={self.server_hostname!r}, "
            f"http_path={self.http_path!r}, "
            f"catalog={self.catalog!r}, "
            f"schema_name={self.schema_name!r}, "
            f"table={self.table!r}, "
            f"access_token={token})"
        )

    def qualified_table(self) -> str:
        catalog = validate_sql_identifier(self.catalog, "catalog")
        schema_name = validate_sql_identifier(self.schema_name, "schema")
        table = validate_sql_identifier(self.table, "table")
        return f"{catalog}.{schema_name}.{table}"

    @classmethod
    def from_environ(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> DatabricksMemoryConfig:
        env = os.environ if environ is None else environ
        hostname = env.get(ENV_SERVER_HOSTNAME, "").strip()
        http_path = env.get(ENV_HTTP_PATH, "").strip()
        catalog = env.get(ENV_CATALOG, "").strip()
        schema_name = env.get(ENV_SCHEMA, "").strip()
        table = env.get(ENV_TABLE, DEFAULT_TABLE).strip() or DEFAULT_TABLE
        token = env.get(ENV_ACCESS_TOKEN) or None
        if token is not None:
            token = token.strip() or None
        missing = [
            name
            for name, value in (
                (ENV_SERVER_HOSTNAME, hostname),
                (ENV_HTTP_PATH, http_path),
                (ENV_CATALOG, catalog),
                (ENV_SCHEMA, schema_name),
            )
            if not value
        ]
        if missing:
            raise DatabricksMemoryConfigError(
                "databricks_config_missing:" + ",".join(missing)
            )
        return cls(
            server_hostname=hostname,
            http_path=http_path,
            catalog=catalog,
            schema_name=schema_name,
            table=table,
            access_token=token,
        )


def validate_sql_identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or IDENTIFIER_RE.fullmatch(value) is None:
        raise DatabricksMemoryConfigError(f"unsafe_sql_identifier:{field}")
    return value


class DatabricksScientificRunMemoryStore:
    """One Delta table. JSON document is the authoritative memory."""

    def __init__(
        self,
        *,
        config: DatabricksMemoryConfig,
        executor: DatabricksSqlExecutor,
    ) -> None:
        self._config = config
        self._executor = executor
        self._table = config.qualified_table()

    def __repr__(self) -> str:
        return (
            "DatabricksScientificRunMemoryStore("
            f"table={self._table!r})"
        )

    @classmethod
    def from_environ(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> DatabricksScientificRunMemoryStore:
        config = DatabricksMemoryConfig.from_environ(environ)
        return cls(
            config=config,
            executor=connect_databricks_sql_executor(config),
        )

    def ensure_schema(self) -> None:
        """Create the table and set Serializable isolation.

        Serializable reduces concurrent NOT MATCHED double-inserts.
        It is not a PRIMARY KEY. Duplicate run_id rows still fail closed.
        """
        self._executor.execute(_create_table_sql(self._table))
        self._executor.execute(
            "ALTER TABLE "
            f"{self._table} "
            "SET TBLPROPERTIES ('delta.isolationLevel' = 'Serializable')"
        )

    def get_run(self, run_id: str) -> ScientificRunMemory | None:
        if not is_safe_run_id(run_id):
            return None
        result = self._executor.execute(
            f"SELECT {READ_COLUMNS} FROM {self._table} "
            "WHERE run_id = :run_id",
            {"run_id": run_id},
        )
        memories = self._memories_from_rows(result.rows)
        if not memories:
            return None
        return memories[0]

    def find_runs_by_target(
        self,
        target_id: str,
    ) -> tuple[ScientificRunMemory, ...]:
        result = self._executor.execute(
            f"SELECT {READ_COLUMNS} FROM {self._table} "
            "WHERE target_id = :target_id "
            "ORDER BY run_id",
            {"target_id": target_id},
        )
        memories = self._memories_from_rows(result.rows)
        return tuple(sorted(memories, key=lambda item: item.run_id))

    def find_candidate(
        self,
        candidate_id: str,
    ) -> tuple[CandidateMemoryHit, ...]:
        result = self._executor.execute(
            f"SELECT {READ_COLUMNS} FROM {self._table}"
        )
        memories = self._memories_from_rows(result.rows)
        hits: list[CandidateMemoryHit] = []
        for memory in memories:
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
        if not is_safe_run_id(memory.run_id):
            raise ScientificRunMemoryError("invalid_run_id")
        document = memory.to_document()
        parameters = _row_parameters(memory, document)
        concurrent_error: DatabricksConcurrentWriteError | None = None
        for _attempt in range(MAX_MERGE_ATTEMPTS):
            try:
                self._executor.execute(_merge_sql(self._table), parameters)
                concurrent_error = None
                break
            except DatabricksConcurrentWriteError as exc:
                concurrent_error = exc
        stored = self.get_run(memory.run_id)
        if stored is None:
            if concurrent_error is not None:
                raise ScientificRunMemoryError(
                    "databricks_concurrent_write_unresolved"
                ) from concurrent_error
            raise ScientificRunMemoryError("databricks_put_failed")
        if stored.memory_digest != memory.memory_digest:
            raise ScientificRunMemoryConflict(
                "scientific_memory_conflict:"
                f"{memory.run_id}:{stored.memory_digest}"
            )
        return stored

    def _memories_from_rows(
        self,
        rows: tuple[dict[str, Any], ...],
    ) -> tuple[ScientificRunMemory, ...]:
        flattened_ids: list[str] = []
        for row in rows:
            run_id = row.get("run_id")
            if not isinstance(run_id, str):
                raise ScientificRunMemoryError(INDEX_MISMATCH_ERROR)
            if run_id in flattened_ids:
                raise ScientificRunMemoryError(f"{DUPLICATE_RUN_ERROR}:{run_id}")
            flattened_ids.append(run_id)
        memories: list[ScientificRunMemory] = []
        seen_json: set[str] = set()
        for row in rows:
            memory = self._row_to_memory(row)
            if memory.run_id in seen_json:
                raise ScientificRunMemoryError(f"{DUPLICATE_RUN_ERROR}:{memory.run_id}")
            seen_json.add(memory.run_id)
            memories.append(memory)
        return tuple(memories)

    def _row_to_memory(self, row: Mapping[str, Any]) -> ScientificRunMemory:
        memory = self._document_to_memory(row.get("memory_document_json"))
        if (
            row.get("run_id") != memory.run_id
            or row.get("memory_digest") != memory.memory_digest
            or row.get("target_id") != memory.target_id
        ):
            raise ScientificRunMemoryError(INDEX_MISMATCH_ERROR)
        return memory

    def _document_to_memory(self, raw: object) -> ScientificRunMemory:
        if not isinstance(raw, str):
            raise ScientificRunMemoryError("scientific_memory_malformed")
        try:
            document = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ScientificRunMemoryError("scientific_memory_unreadable") from exc
        if not isinstance(document, dict):
            raise ScientificRunMemoryError("scientific_memory_malformed")
        try:
            return scientific_run_memory_from_document(document)
        except ScientificRunMemoryError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ScientificRunMemoryError("scientific_memory_malformed") from exc


def _create_table_sql(table: str) -> str:
    return (
        "CREATE TABLE IF NOT EXISTS "
        f"{table} ("
        "schema_version STRING NOT NULL, "
        "memory_id STRING NOT NULL, "
        "run_id STRING NOT NULL, "
        "domain STRING NOT NULL, "
        "memory_house STRING NOT NULL, "
        "epistemic_status STRING NOT NULL, "
        "execution_id STRING NOT NULL, "
        "target_id STRING NOT NULL, "
        "objective STRING NOT NULL, "
        "generator STRING NOT NULL, "
        "scientific_request_sha256 STRING NOT NULL, "
        "request_hash STRING NOT NULL, "
        "manifest_sha256 STRING NOT NULL, "
        "memory_digest STRING NOT NULL, "
        "recorded_at STRING NOT NULL, "
        "memory_document_json STRING NOT NULL"
        ") USING DELTA "
        "TBLPROPERTIES ('delta.isolationLevel' = 'Serializable')"
    )


def _merge_sql(table: str) -> str:
    return (
        "MERGE INTO "
        f"{table} AS target "
        "USING (SELECT "
        ":run_id AS run_id, "
        ":memory_digest AS memory_digest, "
        ":schema_version AS schema_version, "
        ":memory_id AS memory_id, "
        ":domain AS domain, "
        ":memory_house AS memory_house, "
        ":epistemic_status AS epistemic_status, "
        ":execution_id AS execution_id, "
        ":target_id AS target_id, "
        ":objective AS objective, "
        ":generator AS generator, "
        ":scientific_request_sha256 AS scientific_request_sha256, "
        ":request_hash AS request_hash, "
        ":manifest_sha256 AS manifest_sha256, "
        ":recorded_at AS recorded_at, "
        ":memory_document_json AS memory_document_json"
        ") AS source "
        "ON target.run_id = source.run_id "
        "WHEN NOT MATCHED THEN INSERT ("
        "schema_version, memory_id, run_id, domain, memory_house, "
        "epistemic_status, execution_id, target_id, objective, "
        "generator, scientific_request_sha256, request_hash, "
        "manifest_sha256, memory_digest, recorded_at, "
        "memory_document_json"
        ") VALUES ("
        "source.schema_version, source.memory_id, source.run_id, "
        "source.domain, source.memory_house, source.epistemic_status, "
        "source.execution_id, source.target_id, source.objective, "
        "source.generator, source.scientific_request_sha256, "
        "source.request_hash, source.manifest_sha256, "
        "source.memory_digest, source.recorded_at, "
        "source.memory_document_json"
        ")"
    )


def _row_parameters(
    memory: ScientificRunMemory,
    document: dict[str, Any],
) -> dict[str, object]:
    return {
        "schema_version": memory.schema_version,
        "memory_id": memory.memory_id,
        "run_id": memory.run_id,
        "domain": memory.domain,
        "memory_house": memory.memory_house,
        "epistemic_status": memory.epistemic_status,
        "execution_id": memory.execution_id,
        "target_id": memory.target_id,
        "objective": memory.objective,
        "generator": memory.generator,
        "scientific_request_sha256": memory.scientific_request_sha256,
        "request_hash": memory.request_hash,
        "manifest_sha256": memory.manifest_sha256,
        "memory_digest": memory.memory_digest,
        "recorded_at": memory.recorded_at,
        "memory_document_json": json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ),
    }
