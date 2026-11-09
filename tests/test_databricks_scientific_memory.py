from __future__ import annotations

import inspect
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from cellertz.mcp import server as mcp_server
from cellertz.mcp import start_run as mcp_start_run
from cellertz.second_brain.episodic.databricks import (
    DatabricksMemoryConfig,
    DatabricksMemoryConfigError,
    DatabricksScientificRunMemoryStore,
)
from cellertz.second_brain.episodic.databricks.executor import (
    DATABRICKS_OPTIONAL_INSTALL,
    DatabricksConcurrentWriteError,
    DatabricksConnectorExecutor,
    InMemoryDatabricksSqlExecutor,
    connect_databricks_sql_executor,
)
from cellertz.second_brain.episodic.databricks.store import MAX_MERGE_ATTEMPTS
from cellertz.second_brain.episodic.models import (
    ScientificRunMemoryConflict,
    ScientificRunMemoryError,
    attach_memory_digest,
)
from cellertz.second_brain.episodic.store import FileScientificRunMemoryStore

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_scientific_run_memory import project_fixture
from tools.validation.databricks_scientific_memory import (
    parse_args,
    run_databricks_memory,
)

OPERATOR_SOURCE = Path("tools/validation/databricks_scientific_memory.py")
SECRET_TOKEN = "super-secret-databricks-token"
STORE_SOURCE = Path("src/cellertz/second_brain/episodic/databricks")


def _config(**changes: object) -> DatabricksMemoryConfig:
    payload = {
        "server_hostname": "adb.example.azuredatabricks.net",
        "http_path": "/sql/1.0/warehouses/abc",
        "catalog": "cellertz",
        "schema_name": "science",
        "table": "scientific_run_memory",
        "access_token": SECRET_TOKEN,
    }
    payload.update(changes)
    return DatabricksMemoryConfig(**payload)


def _store(
    executor: InMemoryDatabricksSqlExecutor | None = None,
) -> tuple[DatabricksScientificRunMemoryStore, InMemoryDatabricksSqlExecutor]:
    fake = executor or InMemoryDatabricksSqlExecutor()
    return DatabricksScientificRunMemoryStore(config=_config(), executor=fake), fake


def test_databricks_store_satisfies_memory_store_protocol():
    store, _executor = _store()
    assert callable(store.get_run)
    assert callable(store.find_runs_by_target)
    assert callable(store.find_candidate)
    assert callable(store.put)


def test_valid_put_inserts_one_memory(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    stored = store.put(memory)
    assert len(executor.rows) == 1
    assert stored.run_id == memory.run_id
    assert stored.memory_digest == memory.memory_digest


def test_same_run_same_digest_is_idempotent(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    first = store.put(memory)
    second = store.put(replace(memory, recorded_at="2099-01-01T00:00:00+00:00"))
    assert second.memory_digest == first.memory_digest
    assert second.recorded_at == first.recorded_at
    assert len(executor.rows) == 1


def test_same_run_different_digest_raises_conflict(tmp_path):
    memory = project_fixture(tmp_path)
    store, _executor = _store()
    store.put(memory)
    changed = replace(memory.candidates[0], sequence="GGGG")
    conflicting = attach_memory_digest(
        replace(memory, candidates=(changed, *memory.candidates[1:]))
    )
    with pytest.raises(ScientificRunMemoryConflict):
        store.put(conflicting)
    loaded = store.get_run(memory.run_id)
    assert loaded is not None
    assert loaded.candidates[0].sequence == memory.candidates[0].sequence


def test_get_run_reconstructs_exact_scientific_run_memory(tmp_path):
    memory = project_fixture(tmp_path)
    store, _executor = _store()
    store.put(memory)
    loaded = store.get_run(memory.run_id)
    assert loaded is not None
    assert loaded.to_document() == memory.to_document()


def test_corrupted_json_fails_closed(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    store.put(memory)
    _only_row(executor, memory.run_id)["memory_document_json"] = "{"
    with pytest.raises(ScientificRunMemoryError, match="scientific_memory_unreadable"):
        store.get_run(memory.run_id)


def test_stored_digest_mismatch_fails_closed(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    store.put(memory)
    row = _only_row(executor, memory.run_id)
    document = json.loads(row["memory_document_json"])
    document["candidates"][0]["sequence"] = "WWWW"
    row["memory_document_json"] = json.dumps(document)
    with pytest.raises(
        ScientificRunMemoryError,
        match="scientific_memory_digest_mismatch",
    ):
        store.get_run(memory.run_id)


def test_find_runs_by_target_matches_file_store(tmp_path):
    pdl1 = project_fixture(tmp_path)
    bcma = _retarget(pdl1, run_id="protein_run_bcma", target_id="BCMA")
    file_store = FileScientificRunMemoryStore(root=tmp_path / "file")
    databricks, _executor = _store()
    for memory in (pdl1, bcma):
        file_store.put(memory)
        databricks.put(memory)
    file_hits = file_store.find_runs_by_target("PDL1")
    db_hits = databricks.find_runs_by_target("PDL1")
    assert [item.run_id for item in db_hits] == [item.run_id for item in file_hits]
    assert [item.memory_digest for item in db_hits] == [
        item.memory_digest for item in file_hits
    ]


def test_find_candidate_matches_file_store(tmp_path):
    memory = project_fixture(tmp_path)
    file_store = FileScientificRunMemoryStore(root=tmp_path / "file")
    databricks, _executor = _store()
    file_store.put(memory)
    databricks.put(memory)
    file_hits = file_store.find_candidate("cand_b")
    db_hits = databricks.find_candidate("cand_b")
    assert len(db_hits) == len(file_hits) == 1
    assert db_hits[0].run_id == file_hits[0].run_id
    assert db_hits[0].candidate.to_document() == file_hits[0].candidate.to_document()


def test_observed_status_remains_observed(tmp_path):
    memory = project_fixture(tmp_path)
    store, _executor = _store()
    loaded = store.put(memory)
    fetched = store.get_run(memory.run_id)
    assert fetched is not None
    assert loaded.epistemic_status == "observed"
    assert fetched.epistemic_status == "observed"


def test_memory_house_remains_episodic(tmp_path):
    memory = project_fixture(tmp_path)
    store, _executor = _store()
    loaded = store.put(memory)
    fetched = store.get_run(memory.run_id)
    assert fetched is not None
    assert loaded.memory_house == "episodic"
    assert fetched.memory_house == "episodic"


def test_no_semantic_knowledge_package_creation():
    for path in STORE_SOURCE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "KnowledgePackage" not in source
        assert "cellertz.knowledge" not in source
        assert "FileKnowledge" not in source


def test_no_scientific_recomputation():
    for path in STORE_SOURCE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "BindCraftIpSAE" not in source
        assert "project_protein_design_run" not in source
        assert "LocalProteinRunStarter" not in source
        assert "ipsae_mean =" not in source


def test_no_pdb_or_pae_upload():
    for path in STORE_SOURCE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        lowered = source.lower()
        assert "volumes" not in lowered
        assert "upload" not in lowered
        assert ".pdb" not in lowered
        assert "dbfs" not in lowered


def test_sql_identifiers_reject_unsafe_values():
    with pytest.raises(DatabricksMemoryConfigError, match="unsafe_sql_identifier"):
        DatabricksScientificRunMemoryStore(
            config=_config(catalog="cellertz;DROP"),
            executor=InMemoryDatabricksSqlExecutor(),
        )


def test_scientific_query_values_are_parameterized(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    store.put(memory)
    store.get_run(memory.run_id)
    store.find_runs_by_target(memory.target_id)
    for statement, parameters in executor.statements:
        assert "%(" not in statement
        assert memory.run_id not in statement
        assert memory.target_id not in statement
        assert memory.memory_digest not in statement
        assert memory.candidates[0].sequence not in statement
        if "WHERE run_id" in statement:
            assert ":run_id" in statement
            assert parameters["run_id"] == memory.run_id
        if "WHERE target_id" in statement:
            assert ":target_id" in statement
            assert parameters["target_id"] == memory.target_id
        if statement.lstrip().startswith("MERGE"):
            assert ":run_id" in statement
            assert ":memory_digest" in statement
            assert ":memory_document_json" in statement
            assert parameters["run_id"] == memory.run_id


def test_token_never_present_in_repr_or_error():
    config = _config()
    store, _executor = _store()
    assert SECRET_TOKEN not in repr(config)
    assert SECRET_TOKEN not in str(config)
    assert SECRET_TOKEN not in repr(store)
    with pytest.raises(ScientificRunMemoryError, match="databricks_sql_failed") as caught:
        DatabricksConnectorExecutor(_BoomConnection()).execute("SELECT 1")
    assert SECRET_TOKEN not in str(caught.value)


def test_default_operator_mode_is_non_mutating(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    store.put(memory)
    before = list(executor.statements)
    args = parse_args([])
    report = run_databricks_memory(
        args,
        config=_config(),
        executor=executor,
        store=store,
    )
    assert args.ensure_schema is False
    assert args.write_memory is None
    assert report["ok"] is True
    assert report["status"] == "inspect_only"
    assert report["mutation_attempted"] is False
    assert report["config"]["access_token"] == "***"
    assert SECRET_TOKEN not in json.dumps(report)
    assert executor.statements == before


def test_optional_dependency_absence_produces_actionable_error(monkeypatch):
    def boom() -> object:
        raise ImportError("No module named databricks")

    monkeypatch.setattr(
        "cellertz.second_brain.episodic.databricks.executor._import_databricks_sql",
        boom,
    )
    with pytest.raises(ScientificRunMemoryError, match="optional_dependency_missing"):
        connect_databricks_sql_executor(_config())
    try:
        connect_databricks_sql_executor(_config())
    except ScientificRunMemoryError as exc:
        assert "cellertz-ai-coscientist[databricks]" in str(exc)
        assert str(exc) == DATABRICKS_OPTIONAL_INSTALL


def test_backend_equivalence_with_file_store(tmp_path):
    pdl1 = project_fixture(tmp_path)
    bcma = _retarget(pdl1, run_id="protein_run_bcma", target_id="BCMA")
    file_store = FileScientificRunMemoryStore(root=tmp_path / "file")
    databricks, _executor = _store()
    for memory in (pdl1, bcma):
        file_store.put(memory)
        databricks.put(memory)
    for run_id in (pdl1.run_id, bcma.run_id):
        file_memory = file_store.get_run(run_id)
        db_memory = databricks.get_run(run_id)
        assert file_memory is not None
        assert db_memory is not None
        assert file_memory.to_document() == db_memory.to_document()
    assert [
        item.to_document() for item in databricks.find_runs_by_target("PDL1")
    ] == [item.to_document() for item in file_store.find_runs_by_target("PDL1")]
    assert [
        item.to_document() for item in databricks.find_candidate("cand_a")
    ] == [item.to_document() for item in file_store.find_candidate("cand_a")]


def test_get_run_does_not_create_schema(tmp_path):
    store, executor = _store()
    assert store.get_run("protein_run_test") is None
    assert executor.schema_ensured is False
    assert all("CREATE TABLE" not in statement for statement, _params in executor.statements)


def test_operator_ensure_schema_is_explicit():
    executor = InMemoryDatabricksSqlExecutor()
    store = DatabricksScientificRunMemoryStore(config=_config(), executor=executor)
    report = run_databricks_memory(
        parse_args(["--ensure-schema"]),
        config=_config(),
        store=store,
        executor=executor,
    )
    assert report["mutation_attempted"] is True
    assert report["status"] == "schema_ensured"
    assert executor.schema_ensured is True


def test_operator_write_memory(tmp_path):
    memory = project_fixture(tmp_path)
    path = tmp_path / "memory.json"
    path.write_text(json.dumps(memory.to_document()))
    store, executor = _store()
    report = run_databricks_memory(
        parse_args(["--write-memory", str(path)]),
        config=_config(),
        store=store,
        executor=executor,
    )
    assert report["ok"] is True
    assert report["mutation_attempted"] is True
    assert report["run_id"] == memory.run_id
    assert store.get_run(memory.run_id) is not None


def test_operator_is_not_exposed_through_mcp():
    start_source = inspect.getsource(mcp_start_run)
    server_source = inspect.getsource(mcp_server)
    operator_source = OPERATOR_SOURCE.read_text(encoding="utf-8")
    assert "databricks_scientific_memory" not in start_source
    assert "databricks_scientific_memory" not in server_source
    assert "DatabricksScientificRunMemoryStore" not in start_source
    assert "tools.validation" not in start_source
    assert "cellertz.mcp" not in operator_source


def test_native_parameter_markers_used(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    store.put(memory)
    store.get_run(memory.run_id)
    store.find_runs_by_target(memory.target_id)
    named = " ".join(statement for statement, _params in executor.statements)
    assert ":run_id" in named
    assert ":target_id" in named
    assert ":memory_digest" in named
    assert "%(" not in named


def test_no_legacy_pyformat_markers():
    for path in STORE_SOURCE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "%(" not in source


def test_schema_bootstrap_sets_serializable_isolation():
    store, executor = _store()
    store.ensure_schema()
    joined = " ".join(statement for statement, _params in executor.statements)
    assert "delta.isolationLevel" in joined
    assert "Serializable" in joined
    assert any(
        statement.startswith("ALTER TABLE") and "Serializable" in statement
        for statement, _params in executor.statements
    )


def test_schema_contract_is_generated_as_intended():
    store, executor = _store()
    store.ensure_schema()
    create = next(
        statement
        for statement, _params in executor.statements
        if statement.startswith("CREATE TABLE")
    )
    for column in (
        "schema_version",
        "memory_id",
        "run_id",
        "domain",
        "memory_house",
        "epistemic_status",
        "execution_id",
        "target_id",
        "memory_digest",
        "memory_document_json",
    ):
        assert f"{column} STRING NOT NULL" in create
    assert "CHECK" not in create.upper()
    assert "CONSTRAINT" not in create.upper()
    assert "PRIMARY KEY" not in create.upper()
    assert "UNIQUE" not in create.upper()
    assert "delta.isolationLevel" in create
    assert "Serializable" in create


def test_same_digest_existing_row_receives_no_update(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    first = store.put(memory)
    original = dict(_only_row(executor, memory.run_id))
    second = store.put(replace(memory, recorded_at="2099-01-01T00:00:00+00:00"))
    assert second.recorded_at == first.recorded_at
    assert _only_row(executor, memory.run_id) == original
    merge_sql = [
        statement
        for statement, _params in executor.statements
        if statement.lstrip().startswith("MERGE")
    ]
    assert merge_sql
    assert all("UPDATE" not in statement for statement in merge_sql)
    assert all("WHEN NOT MATCHED THEN INSERT" in statement for statement in merge_sql)


def test_duplicate_run_id_rows_fail_closed(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    store.put(memory)
    executor.duplicate_run_row(memory.run_id)
    with pytest.raises(ScientificRunMemoryError, match="scientific_memory_duplicate_run_id"):
        store.get_run(memory.run_id)


def test_put_post_read_detects_duplicate_rows(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    store.put(memory)
    executor.duplicate_run_row(memory.run_id)
    with pytest.raises(ScientificRunMemoryError, match="scientific_memory_duplicate_run_id"):
        store.put(memory)


def test_concurrent_same_digest_resolves_idempotently(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    first = store.put(memory)
    executor.concurrent_merge_failures = MAX_MERGE_ATTEMPTS
    second = store.put(replace(memory, recorded_at="2099-01-01T00:00:00+00:00"))
    assert second.to_document() == first.to_document()
    assert len(executor.rows_for(memory.run_id)) == 1
    merge_count = sum(
        1
        for statement, _params in executor.statements
        if statement.lstrip().startswith("MERGE")
    )
    assert merge_count == 1 + MAX_MERGE_ATTEMPTS


def test_concurrent_different_digest_resolves_as_conflict(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    store.put(memory)
    changed = replace(memory.candidates[0], sequence="GGGG")
    conflicting = attach_memory_digest(
        replace(memory, candidates=(changed, *memory.candidates[1:]))
    )
    executor.concurrent_merge_failures = MAX_MERGE_ATTEMPTS
    with pytest.raises(ScientificRunMemoryConflict):
        store.put(conflicting)
    loaded = store.get_run(memory.run_id)
    assert loaded is not None
    assert loaded.candidates[0].sequence == memory.candidates[0].sequence
    assert len(executor.rows_for(memory.run_id)) == 1


def test_recognized_delta_concurrent_write_handled_safely(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    executor.concurrent_merge_failures = 10
    with pytest.raises(
        ScientificRunMemoryError,
        match="databricks_concurrent_write_unresolved",
    ):
        store.put(memory)
    assert executor.rows == []
    merge_count = sum(
        1
        for statement, _params in executor.statements
        if statement.lstrip().startswith("MERGE")
    )
    assert merge_count == MAX_MERGE_ATTEMPTS
    with pytest.raises(DatabricksConcurrentWriteError, match="databricks_concurrent_write"):
        DatabricksConnectorExecutor(_ConcurrentConnection()).execute(
            "MERGE INTO t AS target USING (SELECT :run_id AS run_id) AS source "
            "ON target.run_id = source.run_id WHEN NOT MATCHED THEN INSERT (run_id) "
            "VALUES (source.run_id)",
            {"run_id": memory.run_id},
        )


def test_unrecognized_databricks_error_propagates_clearly(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    executor.unrecognized_merge_error = "databricks_sql_failed"
    with pytest.raises(ScientificRunMemoryError, match="databricks_sql_failed"):
        store.put(memory)
    merge_count = sum(
        1
        for statement, _params in executor.statements
        if statement.lstrip().startswith("MERGE")
    )
    assert merge_count == 1
    with pytest.raises(ScientificRunMemoryError, match="databricks_sql_failed") as caught:
        DatabricksConnectorExecutor(_BrokenConnection()).execute("SELECT 1")
    assert "warehouse exploded" not in str(caught.value)
    assert SECRET_TOKEN not in str(caught.value)


def test_create_table_contains_no_inline_check_constraints():
    store, executor = _store()
    store.ensure_schema()
    create = next(
        statement
        for statement, _params in executor.statements
        if statement.startswith("CREATE TABLE")
    )
    assert "CHECK" not in create.upper()
    assert "ADD CONSTRAINT" not in " ".join(
        statement for statement, _params in executor.statements
    )


def test_find_runs_by_target_duplicate_run_id_fails_closed(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    store.put(memory)
    executor.duplicate_run_row(memory.run_id)
    with pytest.raises(ScientificRunMemoryError, match="scientific_memory_duplicate_run_id"):
        store.find_runs_by_target(memory.target_id)


def test_find_candidate_duplicate_run_id_fails_closed(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    store.put(memory)
    executor.duplicate_run_row(memory.run_id)
    with pytest.raises(ScientificRunMemoryError, match="scientific_memory_duplicate_run_id"):
        store.find_candidate("cand_a")


def test_flattened_run_id_mismatch_fails_closed(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    store.put(memory)
    _only_row(executor, memory.run_id)["run_id"] = "protein_run_other"
    with pytest.raises(ScientificRunMemoryError, match="scientific_memory_index_mismatch"):
        store.get_run("protein_run_other")


def test_flattened_memory_digest_mismatch_fails_closed(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    store.put(memory)
    _only_row(executor, memory.run_id)["memory_digest"] = "0" * 64
    with pytest.raises(ScientificRunMemoryError, match="scientific_memory_index_mismatch"):
        store.get_run(memory.run_id)


def test_flattened_target_id_mismatch_fails_closed(tmp_path):
    memory = project_fixture(tmp_path)
    store, executor = _store()
    store.put(memory)
    _only_row(executor, memory.run_id)["target_id"] = "NOT_THE_TARGET"
    with pytest.raises(ScientificRunMemoryError, match="scientific_memory_index_mismatch"):
        store.get_run(memory.run_id)


def test_operator_ensure_schema_backend_error_returns_json_failure():
    store = DatabricksScientificRunMemoryStore(
        config=_config(),
        executor=_FailingExecutor(),
    )
    report = run_databricks_memory(
        parse_args(["--ensure-schema"]),
        config=_config(),
        store=store,
    )
    assert report["ok"] is False
    assert report["status"] == "failed"
    assert report["error"] == "databricks_sql_failed"
    assert report["mutation_attempted"] is True
    assert SECRET_TOKEN not in json.dumps(report)


def test_operator_get_run_backend_error_returns_json_failure():
    store = DatabricksScientificRunMemoryStore(
        config=_config(),
        executor=_FailingExecutor(),
    )
    report = run_databricks_memory(
        parse_args(["--get-run", "protein_run_test"]),
        config=_config(),
        store=store,
    )
    assert report["ok"] is False
    assert report["status"] == "failed"
    assert report["error"] == "databricks_sql_failed"
    assert report["mutation_attempted"] is False
    assert SECRET_TOKEN not in json.dumps(report)


def test_operator_malformed_write_memory_returns_controlled_failure(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")
    store, executor = _store()
    report = run_databricks_memory(
        parse_args(["--write-memory", str(path)]),
        config=_config(),
        store=store,
        executor=executor,
    )
    assert report["ok"] is False
    assert report["status"] == "failed"
    assert report["error"] == "scientific_memory_unreadable"
    assert report["mutation_attempted"] is True
    assert SECRET_TOKEN not in json.dumps(report)
    assert executor.rows == []


def _only_row(executor: InMemoryDatabricksSqlExecutor, run_id: str) -> dict[str, object]:
    matches = executor.rows_for(run_id)
    assert len(matches) == 1
    return matches[0]


def _retarget(memory, *, run_id: str, target_id: str):
    execution = replace(memory.execution, run_id=run_id)
    return attach_memory_digest(
        replace(
            memory,
            run_id=run_id,
            memory_id=f"protein_design:{run_id}",
            target_id=target_id,
            execution=execution,
        )
    )


class _BoomConnection:
    def cursor(self):
        raise RuntimeError(f"rejected token={SECRET_TOKEN}")


class _ConcurrentCursor:
    description = None
    rowcount = 0

    def execute(self, statement, parameters=None):
        raise RuntimeError("ConcurrentAppendException: Files were added")

    def close(self):
        return None

    def fetchall(self):
        return []


class _ConcurrentConnection:
    def cursor(self):
        return _ConcurrentCursor()


class _BrokenCursor:
    description = None
    rowcount = 0

    def execute(self, statement, parameters=None):
        raise RuntimeError(f"warehouse exploded token={SECRET_TOKEN}")

    def close(self):
        return None


class _BrokenConnection:
    def cursor(self):
        return _BrokenCursor()


class _FailingExecutor:
    def execute(self, statement, parameters=None):
        raise ScientificRunMemoryError("databricks_sql_failed")
