from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from cellertz.mcp.capabilities import cellertz_capabilities
from cellertz.mcp.episodic_memory import (
    INTERPRETATION,
    get_scientific_memory_reader,
    scientific_memory_find_candidate,
    scientific_memory_find_runs_by_target,
    scientific_memory_get_run,
    set_scientific_memory_reader,
)
from cellertz.mcp.server import mcp
from cellertz.second_brain.episodic.databricks import (
    DatabricksMemoryConfig,
    InMemoryDatabricksSqlExecutor,
)
from cellertz.second_brain.episodic.factory import (
    create_scientific_run_memory_store,
)
from cellertz.second_brain.episodic.models import (
    ScientificRunMemoryError,
    attach_memory_digest,
)
from cellertz.second_brain.episodic.store import FileScientificRunMemoryStore

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_scientific_run_memory import project_fixture

SECRET = "super-secret-databricks-token"
VAULT = Path("second_brain")
HERMES_SKILL = Path("hermes/skills/cellertz-second-brain/SKILL.md")


@pytest.fixture(autouse=True)
def _reset_reader():
    set_scientific_memory_reader(None)
    yield
    set_scientific_memory_reader(None)


def _config() -> DatabricksMemoryConfig:
    return DatabricksMemoryConfig(
        server_hostname="adb.example.invalid",
        http_path="/sql/1.0/warehouses/abc",
        catalog="cellertz",
        schema_name="science",
        table="scientific_run_memory",
        access_token=SECRET,
    )


def _file_store(tmp_path: Path, *memories):
    store = FileScientificRunMemoryStore(root=tmp_path / "memory")
    for memory in memories:
        store.put(memory)
    set_scientific_memory_reader(store)
    return store


def _db_store(*memories):
    executor = InMemoryDatabricksSqlExecutor()
    store = create_scientific_run_memory_store(
        backend="databricks",
        config=_config(),
        executor=executor,
    )
    for memory in memories:
        store.put(memory)
    executor.statements.clear()
    set_scientific_memory_reader(store)
    return store, executor


def _retarget(memory, *, run_id: str, target_id: str | None = None):
    execution = replace(memory.execution, run_id=run_id)
    updated = replace(
        memory,
        run_id=run_id,
        memory_id=f"protein_design:{run_id}",
        execution=execution,
    )
    if target_id is not None:
        updated = replace(updated, target_id=target_id)
    return attach_memory_digest(updated)


def _hashes(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            found[path.relative_to(root).as_posix()] = digest
    return found


def _assert_observed(payload: dict) -> None:
    assert payload["response_schema"] == "episodic_retrieval_v1"
    assert payload["memory_house"] == "episodic"
    assert payload["epistemic_status"] == "observed"
    assert payload["interpretation"] == INTERPRETATION


def _call(name: str, arguments: dict) -> dict:
    result = asyncio.run(mcp.call_tool(name, arguments))
    assert result.is_error is False
    return json.loads(result.content[0].text)


def test_file_and_databricks_reads_found_and_absent(tmp_path):
    memory = project_fixture(tmp_path)
    other = _retarget(memory, run_id="protein_run_bcma", target_id="BCMA")
    for opener in (
        lambda: _file_store(tmp_path, memory, other),
        lambda: _db_store(memory, other)[0],
    ):
        opener()
        found = scientific_memory_get_run(memory.run_id)
        missing = scientific_memory_get_run("protein_run_absent")
        _assert_observed(found)
        _assert_observed(missing)
        assert found["ok"] is True and found["found"] is True
        assert found["memory"] == memory.to_document()
        assert found["memory_digest"] == memory.memory_digest
        assert found["memory"]["candidates"][0]["ranking_score"] == (
            memory.candidates[0].ranking_score
        )
        assert missing["ok"] is True and missing["found"] is False
        assert missing["memory"] is None
        assert missing["memory_digest"] is None
        by_target = scientific_memory_find_runs_by_target("PDL1")
        absent_target = scientific_memory_find_runs_by_target("pdl1")
        by_candidate = scientific_memory_find_candidate("cand_a")
        absent_candidate = scientific_memory_find_candidate("cand_missing")
        for payload in (by_target, absent_target, by_candidate, absent_candidate):
            _assert_observed(payload)
        assert by_target["found"] is True
        assert [item["run_id"] for item in by_target["items"]] == [memory.run_id]
        assert "candidates" not in by_target["items"][0]
        assert by_target["items"][0]["candidate_count"] == len(memory.candidates)
        assert absent_target["ok"] is True and absent_target["found"] is False
        assert absent_target["items"] == []
        assert by_candidate["found"] is True
        assert by_candidate["items"][0]["candidate"]["candidate_id"] == "cand_a"
        assert "memory_digest" not in by_candidate["items"][0]
        assert absent_candidate["found"] is False
        assert absent_candidate["items"] == []


def test_candidate_occurrences_stay_ordered_across_runs(tmp_path):
    first = project_fixture(tmp_path)
    second = _retarget(first, run_id="protein_run_later")
    earlier = _retarget(first, run_id="protein_run_earlier")
    _file_store(tmp_path, second, earlier)
    payload = scientific_memory_find_candidate("cand_a", limit=20)
    assert [item["run_id"] for item in payload["items"]] == [
        "protein_run_earlier",
        "protein_run_later",
    ]
    assert payload["total_count"] == 2
    assert payload["returned_count"] == 2
    assert payload["truncated"] is False


def test_limit_counts_and_invalid_inputs(tmp_path):
    memory = project_fixture(tmp_path)
    other = _retarget(memory, run_id="protein_run_other")
    _file_store(tmp_path, other, memory)
    page = scientific_memory_find_runs_by_target("PDL1", limit=1)
    assert page["returned_count"] == 1
    assert page["total_count"] == 2
    assert page["limit"] == 1
    assert page["truncated"] is True
    assert page["items"][0]["run_id"] == "protein_run_other"
    for bad in (True, False, 0, 101, "20", 1.5):
        denied = scientific_memory_find_candidate("cand_a", limit=bad)
        _assert_observed(denied)
        assert denied["ok"] is False
        assert denied["found"] is False
        assert denied["error"] == "invalid_limit"
        assert denied["items"] == []
    for denied in (
        scientific_memory_get_run(" "),
        scientific_memory_get_run("../etc/passwd"),
        scientific_memory_get_run("/tmp/run"),
        scientific_memory_get_run(None),
    ):
        _assert_observed(denied)
        assert denied["error"] == "invalid_run_id"
        assert denied["found"] is False
    denied_target = scientific_memory_find_runs_by_target("  ")
    denied_candidate = scientific_memory_find_candidate("")
    _assert_observed(denied_target)
    _assert_observed(denied_candidate)
    assert denied_target["error"] == "invalid_target_id"
    assert denied_candidate["error"] == "invalid_candidate_id"


def test_invalid_input_does_not_construct_reader(monkeypatch):
    calls: list[object] = []

    def boom(**kwargs: object) -> object:
        calls.append(kwargs)
        raise AssertionError("reader constructed")

    monkeypatch.setattr(
        "cellertz.mcp.episodic_memory.create_scientific_run_memory_store",
        boom,
    )
    scientific_memory_get_run("../secret")
    scientific_memory_find_runs_by_target("   ")
    scientific_memory_find_candidate("cand_a", limit=True)
    assert calls == []
    assert get_scientific_memory_reader is not None


def test_reads_do_not_mutate_storage_or_vault(tmp_path):
    memory = project_fixture(tmp_path)
    store = _file_store(tmp_path, memory)
    before_memory = _hashes(store.root)
    before_vault = _hashes(VAULT)
    scientific_memory_get_run(memory.run_id)
    scientific_memory_find_runs_by_target(memory.target_id)
    scientific_memory_find_candidate("cand_b")
    assert _hashes(store.root) == before_memory
    assert _hashes(VAULT) == before_vault


def test_missing_file_root_stays_absent(tmp_path):
    root = tmp_path / "missing-memory"
    set_scientific_memory_reader(FileScientificRunMemoryStore(root=root))
    payload = scientific_memory_get_run("protein_run_test")
    listed = scientific_memory_find_runs_by_target("PDL1")
    assert payload["found"] is False
    assert listed["found"] is False
    assert not root.exists()


def test_reader_write_methods_are_not_called(tmp_path):
    memory = project_fixture(tmp_path)
    inner = _file_store(tmp_path, memory)

    class _Spy:
        def get_run(self, run_id: str):
            return inner.get_run(run_id)

        def find_runs_by_target(self, target_id: str):
            return inner.find_runs_by_target(target_id)

        def find_candidate(self, candidate_id: str):
            return inner.find_candidate(candidate_id)

        def put(self, memory):
            raise AssertionError("put")

        def ensure_schema(self):
            raise AssertionError("ensure_schema")

    set_scientific_memory_reader(_Spy())
    assert scientific_memory_get_run(memory.run_id)["found"] is True
    assert scientific_memory_find_runs_by_target("PDL1")["found"] is True
    assert scientific_memory_find_candidate("cand_a")["found"] is True


def test_databricks_reads_are_select_only(tmp_path):
    memory = project_fixture(tmp_path)
    _store, executor = _db_store(memory)
    scientific_memory_get_run(memory.run_id)
    scientific_memory_find_runs_by_target("PDL1")
    scientific_memory_find_candidate("cand_a")
    assert executor.statements
    for statement, _params in executor.statements:
        head = statement.lstrip().upper()
        assert head.startswith("SELECT")
        assert "MERGE" not in head
        assert "INSERT" not in head
        assert "UPDATE" not in head
        assert "CREATE" not in head
        assert "ALTER" not in head


def test_integrity_failures_are_not_absence(tmp_path):
    memory = project_fixture(tmp_path)
    _store, executor = _db_store(memory)
    row = executor.rows_for(memory.run_id)[0]
    original = row["memory_document_json"]
    row["memory_document_json"] = "{"
    broken = scientific_memory_get_run(memory.run_id)
    _assert_observed(broken)
    assert broken["ok"] is False
    assert broken["found"] is False
    assert broken["error"] == "scientific_memory_unreadable"
    assert broken["memory"] is None
    row["memory_document_json"] = original
    document = json.loads(row["memory_document_json"])
    document["candidates"][0]["sequence"] = "WWWW"
    row["memory_document_json"] = json.dumps(document)
    mismatched = scientific_memory_get_run(memory.run_id)
    _assert_observed(mismatched)
    assert mismatched["ok"] is False
    assert mismatched["error"] == "scientific_memory_digest_mismatch"
    executor.duplicate_run_row(memory.run_id)
    duplicate = scientific_memory_find_candidate("cand_a")
    _assert_observed(duplicate)
    assert duplicate["ok"] is False
    assert duplicate["found"] is False
    assert duplicate["error"] == "scientific_memory_duplicate_run_id"
    assert duplicate["items"] == []


def test_backend_errors_do_not_leak_or_fall_back(tmp_path, monkeypatch):
    memory = project_fixture(tmp_path)
    root = tmp_path / "file"
    file_store = FileScientificRunMemoryStore(root=root)
    file_store.put(memory)

    def boom(_config: object) -> object:
        raise RuntimeError(f"rejected token={SECRET}")

    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_MEMORY_ROOT", str(root))
    monkeypatch.setenv("CELLERTZ_SCIENTIFIC_MEMORY_BACKEND", "databricks")
    monkeypatch.setenv("CELLERTZ_DATABRICKS_SERVER_HOSTNAME", "adb.example.invalid")
    monkeypatch.setenv("CELLERTZ_DATABRICKS_HTTP_PATH", "/sql/1.0/warehouses/abc")
    monkeypatch.setenv("CELLERTZ_DATABRICKS_CATALOG", "cellertz")
    monkeypatch.setenv("CELLERTZ_DATABRICKS_SCHEMA", "science")
    monkeypatch.setenv("CELLERTZ_DATABRICKS_ACCESS_TOKEN", SECRET)
    monkeypatch.setattr(
        "cellertz.second_brain.episodic.databricks.executor.connect_databricks_sql_executor",
        boom,
    )
    payload = scientific_memory_get_run(memory.run_id)
    encoded = json.dumps(payload)
    _assert_observed(payload)
    assert payload["ok"] is False
    assert payload["found"] is False
    assert payload["error"] == "scientific_memory_unavailable"
    assert payload["memory"] is None
    assert payload["memory_digest"] is None
    assert payload["run_id"] == memory.run_id
    assert SECRET not in encoded
    assert "adb.example.invalid" not in encoded
    stored = file_store.get_run(memory.run_id)
    assert stored is not None
    assert stored.memory_digest == memory.memory_digest


def test_registration_and_file_reads_do_not_connect(monkeypatch, tmp_path):
    def boom(_config: object) -> object:
        raise AssertionError("databricks connected")

    monkeypatch.setattr(
        "cellertz.second_brain.episodic.databricks.executor.connect_databricks_sql_executor",
        boom,
    )
    monkeypatch.setattr(
        "cellertz.second_brain.episodic.databricks.executor._import_databricks_sql",
        boom,
    )
    assert cellertz_capabilities()["episodic_memory"]["write"] is False
    tools = asyncio.run(mcp.list_tools())
    names = {tool.name: tool for tool in tools}
    assert set(names["scientific_memory_get_run"].input_schema["properties"]) == {
        "run_id"
    }
    assert set(
        names["scientific_memory_find_runs_by_target"].input_schema["properties"]
    ) == {"target_id", "limit"}
    assert set(
        names["scientific_memory_find_candidate"].input_schema["properties"]
    ) == {"candidate_id", "limit"}
    memory = project_fixture(tmp_path)
    other = _retarget(memory, run_id="protein_run_other")
    _file_store(tmp_path, other, memory)
    found = _call("scientific_memory_get_run", {"run_id": memory.run_id})
    missing = _call("scientific_memory_get_run", {"run_id": "protein_run_absent"})
    by_target = _call(
        "scientific_memory_find_runs_by_target",
        {"target_id": "PDL1", "limit": 1},
    )
    absent_target = _call(
        "scientific_memory_find_runs_by_target",
        {"target_id": "pdl1"},
    )
    by_candidate = _call(
        "scientific_memory_find_candidate",
        {"candidate_id": "cand_a", "limit": 1},
    )
    absent_candidate = _call(
        "scientific_memory_find_candidate",
        {"candidate_id": "cand_missing"},
    )
    for payload in (
        found,
        missing,
        by_target,
        absent_target,
        by_candidate,
        absent_candidate,
    ):
        _assert_observed(payload)
    assert found["found"] is True
    assert found["run_id"] == memory.run_id
    assert found["memory_digest"] == memory.memory_digest
    assert missing["found"] is False
    assert missing["run_id"] == "protein_run_absent"
    assert missing["memory"] is None
    assert by_target["target_id"] == "PDL1"
    assert by_target["limit"] == 1
    assert by_target["returned_count"] == 1
    assert by_target["total_count"] == 2
    assert by_target["truncated"] is True
    assert by_target["items"][0]["run_id"] == "protein_run_other"
    assert absent_target["found"] is False
    assert absent_target["target_id"] == "pdl1"
    assert absent_target["items"] == []
    assert by_candidate["candidate_id"] == "cand_a"
    assert by_candidate["limit"] == 1
    assert by_candidate["returned_count"] == 1
    assert by_candidate["total_count"] == 2
    assert by_candidate["truncated"] is True
    assert absent_candidate["found"] is False
    assert absent_candidate["candidate_id"] == "cand_missing"
    assert absent_candidate["items"] == []
    assert "scientific_memory_get_run" not in HERMES_SKILL.read_text(encoding="utf-8")


def test_retrieval_ignores_mutation_mode(monkeypatch, tmp_path):
    memory = project_fixture(tmp_path)
    _file_store(tmp_path, memory)
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "disabled")
    disabled = scientific_memory_get_run(memory.run_id)
    assert cellertz_capabilities()["mutation"]["enabled"] is False
    monkeypatch.setenv("CELLERTZ_MCP_MUTATION_MODE", "fixture_only")
    enabled = scientific_memory_get_run(memory.run_id)
    assert cellertz_capabilities()["mutation"] == {
        "enabled": True,
        "mode": "fixture_only",
        "real_generators_allowed": False,
    }
    assert disabled["memory_digest"] == enabled["memory_digest"]
    assert disabled["found"] is True


def test_safe_error_hides_scientific_exception_suffix():
    class _Reader:
        def get_run(self, run_id: str):
            raise ScientificRunMemoryError(f"databricks_sql_failed:{SECRET}")

        def find_runs_by_target(self, target_id: str):
            return ()

        def find_candidate(self, candidate_id: str):
            return ()

    set_scientific_memory_reader(_Reader())
    payload = scientific_memory_get_run("protein_run_test")
    _assert_observed(payload)
    assert payload["error"] == "databricks_sql_failed"
    assert SECRET not in json.dumps(payload)


def test_mcp_invalid_limits_do_not_touch_backend(monkeypatch):
    calls: list[object] = []

    def boom(**kwargs: object) -> object:
        calls.append(kwargs)
        raise AssertionError("reader constructed")

    monkeypatch.setattr(
        "cellertz.mcp.episodic_memory.create_scientific_run_memory_store",
        boom,
    )
    cases = (
        ("scientific_memory_find_candidate", "candidate_id", "cand_a"),
        ("scientific_memory_find_runs_by_target", "target_id", "PDL1"),
    )
    for limit in (True, 0, 101):
        for name, key, identifier in cases:
            payload = _call(name, {key: identifier, "limit": limit})
            _assert_observed(payload)
            assert payload["ok"] is False
            assert payload["found"] is False
            assert payload["error"] == "invalid_limit"
            assert payload["items"] == []
            assert payload["limit"] is limit
            assert payload[key] == identifier
    assert calls == []


def test_import_and_listing_do_not_connect_databricks():
    script = """
import asyncio
import builtins
import json
import os

os.environ["CELLERTZ_SCIENTIFIC_MEMORY_BACKEND"] = "databricks"
os.environ["CELLERTZ_DATABRICKS_SERVER_HOSTNAME"] = "adb.example.invalid"
os.environ["CELLERTZ_DATABRICKS_HTTP_PATH"] = "/sql/1.0/warehouses/abc"
os.environ["CELLERTZ_DATABRICKS_CATALOG"] = "cellertz"
os.environ["CELLERTZ_DATABRICKS_SCHEMA"] = "science"
os.environ["CELLERTZ_DATABRICKS_ACCESS_TOKEN"] = "super-secret-databricks-token"

_real_import = builtins.__import__

def _guard(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "databricks" or name.startswith("databricks."):
        raise RuntimeError("databricks connector imported")
    module = _real_import(name, globals, locals, fromlist, level)
    if name == "cellertz.second_brain.episodic.databricks.executor":
        def boom(_config):
            raise RuntimeError("databricks connected")
        module.connect_databricks_sql_executor = boom
    return module

builtins.__import__ = _guard

from cellertz.mcp.capabilities import cellertz_capabilities
from cellertz.mcp.server import mcp

capabilities = cellertz_capabilities()["episodic_memory"]
tools = asyncio.run(mcp.list_tools())
print(json.dumps({
    "write": capabilities["write"],
    "tools": sorted(tool.name for tool in tools),
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    reported = json.loads(completed.stdout)
    assert reported["write"] is False
    assert {
        "scientific_memory_get_run",
        "scientific_memory_find_runs_by_target",
        "scientific_memory_find_candidate",
        "cellertz_get_capabilities",
    } <= set(reported["tools"])
