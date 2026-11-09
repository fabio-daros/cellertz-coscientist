from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

from cellertz.second_brain.episodic.databricks import (
    DatabricksMemoryConfig,
    DatabricksScientificRunMemoryStore,
    InMemoryDatabricksSqlExecutor,
)
from cellertz.second_brain.episodic.factory import (
    BACKEND_DATABRICKS,
    BACKEND_FILE,
    SCIENTIFIC_MEMORY_BACKEND_ENV,
    create_scientific_run_memory_store,
    resolve_scientific_memory_backend,
)
from cellertz.second_brain.episodic.models import (
    ScientificRunMemoryConflict,
    ScientificRunMemoryError,
    attach_memory_digest,
)
from cellertz.second_brain.episodic.store import FileScientificRunMemoryStore

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_scientific_run_memory import project_fixture, write_complete_run
from tools.validation.second_brain_ingest_protein_run import (
    ingest_protein_run,
    parse_args,
)

FACTORY_SOURCE = Path("src/cellertz/second_brain/episodic/factory.py")
OPERATOR_SOURCE = Path("tools/validation/second_brain_ingest_protein_run.py")
SEMANTIC_VAULT = Path("second_brain")


def _databricks_config() -> DatabricksMemoryConfig:
    return DatabricksMemoryConfig(
        server_hostname="adb.example.azuredatabricks.net",
        http_path="/sql/1.0/warehouses/abc",
        catalog="cellertz",
        schema_name="science",
        table="scientific_run_memory",
        access_token="super-secret-databricks-token",
    )


def test_backend_selector_default_is_file():
    assert resolve_scientific_memory_backend(environ={}) == BACKEND_FILE
    store = create_scientific_run_memory_store(environ={})
    assert isinstance(store, FileScientificRunMemoryStore)


def test_explicit_file_backend():
    assert resolve_scientific_memory_backend("file") == BACKEND_FILE
    store = create_scientific_run_memory_store(backend="file")
    assert isinstance(store, FileScientificRunMemoryStore)


def test_explicit_databricks_backend():
    executor = InMemoryDatabricksSqlExecutor()
    store = create_scientific_run_memory_store(
        backend="databricks",
        config=_databricks_config(),
        executor=executor,
    )
    assert resolve_scientific_memory_backend("databricks") == BACKEND_DATABRICKS
    assert isinstance(store, DatabricksScientificRunMemoryStore)


def test_unknown_backend_is_rejected():
    with pytest.raises(
        ScientificRunMemoryError,
        match="unknown_scientific_memory_backend:s3",
    ):
        resolve_scientific_memory_backend("s3")
    with pytest.raises(
        ScientificRunMemoryError,
        match="unknown_scientific_memory_backend:s3",
    ):
        create_scientific_run_memory_store(backend="s3")


def test_cli_overrides_environment_backend():
    environ = {SCIENTIFIC_MEMORY_BACKEND_ENV: "databricks"}
    assert resolve_scientific_memory_backend("file", environ) == BACKEND_FILE
    assert resolve_scientific_memory_backend(None, environ) == BACKEND_DATABRICKS


def test_file_factory_does_not_import_databricks_connector():
    source = FACTORY_SOURCE.read_text(encoding="utf-8")
    assert "from databricks import" not in source
    store = create_scientific_run_memory_store(backend="file")
    assert isinstance(store, FileScientificRunMemoryStore)


def test_explicit_databricks_config_is_not_replaced_by_environ(monkeypatch):
    explicit = DatabricksMemoryConfig(
        server_hostname="explicit.example.invalid",
        http_path="/sql/1.0/warehouses/explicit",
        catalog="explicit_catalog",
        schema_name="explicit_schema",
        table="explicit_table",
        access_token="explicit-token",
    )
    environ = {
        "CELLERTZ_DATABRICKS_SERVER_HOSTNAME": "env.example.invalid",
        "CELLERTZ_DATABRICKS_HTTP_PATH": "/sql/1.0/warehouses/env",
        "CELLERTZ_DATABRICKS_CATALOG": "env_catalog",
        "CELLERTZ_DATABRICKS_SCHEMA": "env_schema",
        "CELLERTZ_DATABRICKS_TABLE": "env_table",
        "CELLERTZ_DATABRICKS_ACCESS_TOKEN": "env-token",
    }
    captured: dict[str, DatabricksMemoryConfig] = {}

    def fake_connect(config: DatabricksMemoryConfig) -> InMemoryDatabricksSqlExecutor:
        captured["config"] = config
        return InMemoryDatabricksSqlExecutor()

    monkeypatch.setattr(
        "cellertz.second_brain.episodic.databricks.executor.connect_databricks_sql_executor",
        fake_connect,
    )
    store = create_scientific_run_memory_store(
        backend="databricks",
        config=explicit,
        environ=environ,
    )
    assert isinstance(store, DatabricksScientificRunMemoryStore)
    assert captured["config"] is explicit
    assert captured["config"].catalog == "explicit_catalog"
    assert captured["config"].schema_name == "explicit_schema"
    assert captured["config"].table == "explicit_table"
    assert captured["config"].catalog != "env_catalog"
    assert "explicit_catalog.explicit_schema.explicit_table" in repr(store)
    assert "env_catalog" not in repr(store)
    assert "env-token" not in repr(store)
    assert "explicit-token" not in repr(store)


def test_inspect_only_databricks_does_not_open_store(tmp_path, monkeypatch):
    artifact_dir, output_dir = write_complete_run(tmp_path)

    def boom(**_kwargs: object) -> object:
        raise AssertionError("store must not be created during inspect-only")

    def boom_connect(_config: object) -> object:
        raise AssertionError("databricks must not connect during inspect-only")

    monkeypatch.setattr(
        "cellertz.second_brain.episodic.databricks.executor.connect_databricks_sql_executor",
        boom_connect,
    )
    args = parse_args(
        [
            "--run-artifact-dir",
            str(artifact_dir),
            "--execution-output-dir",
            str(output_dir),
            "--backend",
            "databricks",
        ]
    )
    report = ingest_protein_run(args, store_factory=boom)
    assert args.persist is False
    assert report["ok"] is True
    assert report["status"] == "inspect_only"
    assert report["persist_attempted"] is False
    assert report["backend"] == "databricks"
    assert report["memory_house"] == "episodic"
    assert report["epistemic_status"] == "observed"
    assert "memory_path" not in report


def test_file_persistence_regression(tmp_path):
    memory = project_fixture(tmp_path)
    store = create_scientific_run_memory_store(
        backend="file",
        file_root=tmp_path / "memory",
    )
    first = store.put(memory)
    second = store.put(memory)
    assert first.memory_digest == second.memory_digest
    assert second.recorded_at == first.recorded_at
    changed = replace(memory.candidates[0], sequence="GGGG")
    conflicting = attach_memory_digest(
        replace(memory, candidates=(changed, *memory.candidates[1:]))
    )
    with pytest.raises(ScientificRunMemoryConflict):
        store.put(conflicting)
    loaded = store.get_run(memory.run_id)
    assert loaded is not None
    assert loaded.candidates[0].sequence == memory.candidates[0].sequence


def test_databricks_persist_through_store_contract(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    executor = InMemoryDatabricksSqlExecutor()
    store = create_scientific_run_memory_store(
        backend="databricks",
        config=_databricks_config(),
        executor=executor,
    )
    args = parse_args(
        [
            "--run-artifact-dir",
            str(artifact_dir),
            "--execution-output-dir",
            str(output_dir),
            "--backend",
            "databricks",
            "--persist",
        ]
    )
    report = ingest_protein_run(args, store=store)
    assert report["ok"] is True
    assert report["backend"] == "databricks"
    assert report["persist_attempted"] is True
    assert report["status"] == "persisted"
    assert report["memory_house"] == "episodic"
    assert report["epistemic_status"] == "observed"
    assert "memory_path" not in report
    assert "access_token" not in report
    assert "super-secret-databricks-token" not in str(report)
    stored = store.get_run(report["run_id"])
    assert stored is not None
    assert stored.memory_digest == report["memory_digest"]
    again = ingest_protein_run(args, store=store)
    assert again["ok"] is True
    assert again["memory_digest"] == report["memory_digest"]
    assert len(executor.rows) == 1


def test_operator_cli_overrides_env_to_file(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    memory_root = tmp_path / "memory"
    args = parse_args(
        [
            "--run-artifact-dir",
            str(artifact_dir),
            "--execution-output-dir",
            str(output_dir),
            "--backend",
            "file",
            "--memory-root",
            str(memory_root),
            "--persist",
        ]
    )
    report = ingest_protein_run(
        args,
        environ={SCIENTIFIC_MEMORY_BACKEND_ENV: "databricks"},
    )
    assert report["backend"] == "file"
    assert report["status"] == "persisted"
    assert Path(report["memory_path"]).is_file()


def test_persist_does_not_mutate_semantic_vault(tmp_path):
    before = sorted(
        path.relative_to(SEMANTIC_VAULT).as_posix()
        for path in SEMANTIC_VAULT.rglob("*")
        if path.is_file()
    )
    artifact_dir, output_dir = write_complete_run(tmp_path)
    args = parse_args(
        [
            "--run-artifact-dir",
            str(artifact_dir),
            "--execution-output-dir",
            str(output_dir),
            "--memory-root",
            str(tmp_path / "memory"),
            "--persist",
        ]
    )
    report = ingest_protein_run(args)
    assert report["ok"] is True
    after = sorted(
        path.relative_to(SEMANTIC_VAULT).as_posix()
        for path in SEMANTIC_VAULT.rglob("*")
        if path.is_file()
    )
    assert after == before
    operator_source = OPERATOR_SOURCE.read_text(encoding="utf-8")
    assert "KnowledgePackage" in operator_source
    assert "second_brain/packages" not in operator_source
    assert "LocalProteinRunStarter" not in operator_source
