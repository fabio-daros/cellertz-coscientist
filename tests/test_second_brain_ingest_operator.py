from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

from cellertz.mcp import server as mcp_server
from cellertz.mcp import start_run as mcp_start_run
from cellertz.second_brain.episodic.store import (
    MEMORY_FILENAME,
    FileScientificRunMemoryStore,
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_scientific_run_memory import write_complete_run
from tools.validation.second_brain_ingest_protein_run import (
    ingest_protein_run,
    parse_args,
)

OPERATOR_SOURCE = Path("tools/validation/second_brain_ingest_protein_run.py")


def test_operator_defaults_to_inspect_only(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    memory_root = tmp_path / "memory"
    args = parse_args(
        [
            "--run-artifact-dir",
            str(artifact_dir),
            "--execution-output-dir",
            str(output_dir),
            "--memory-root",
            str(memory_root),
        ]
    )
    assert args.persist is False
    report = ingest_protein_run(args)
    assert report["ok"] is True
    assert report["status"] == "inspect_only"
    assert report["persist_attempted"] is False
    assert report["memory_digest"]
    assert report["backend"] == "file"
    assert report["memory_house"] == "episodic"
    assert report["epistemic_status"] == "observed"
    store = FileScientificRunMemoryStore(root=memory_root)
    assert store.get_run("protein_run_test") is None
    assert not (memory_root / "runs").exists()


def test_operator_persist_creates_memory(tmp_path):
    artifact_dir, output_dir = write_complete_run(tmp_path)
    memory_root = tmp_path / "memory"
    args = parse_args(
        [
            "--run-artifact-dir",
            str(artifact_dir),
            "--execution-output-dir",
            str(output_dir),
            "--memory-root",
            str(memory_root),
            "--persist",
        ]
    )
    report = ingest_protein_run(args)
    assert report["ok"] is True
    assert report["backend"] == "file"
    assert report["persist_attempted"] is True
    assert report["status"] == "persisted"
    path = Path(report["memory_path"])
    assert path.name == MEMORY_FILENAME
    assert path.is_file()
    stored = FileScientificRunMemoryStore(root=memory_root).get_run("protein_run_test")
    assert stored is not None
    assert stored.memory_digest == report["memory_digest"]
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["run_id"] == "protein_run_test"
    assert document["memory_house"] == "episodic"
    assert document["epistemic_status"] == "observed"
    assert document["candidates"][0]["sequence"]


def test_operator_is_not_exposed_through_mcp():
    start_source = inspect.getsource(mcp_start_run)
    server_source = inspect.getsource(mcp_server)
    operator_source = OPERATOR_SOURCE.read_text(encoding="utf-8")
    assert "second_brain_ingest" not in start_source
    assert "second_brain_ingest" not in server_source
    assert "ingest_protein_run" not in start_source
    assert "ingest_protein_run" not in server_source
    assert "ScientificRunMemory" not in start_source
    assert "ScientificRunMemory" not in server_source
    assert "FileScientificRunMemoryStore" not in start_source
    assert "FileScientificRunMemoryStore" not in server_source
    assert "FileScientificMemoryStore" not in start_source
    assert "FileScientificMemoryStore" not in server_source
    assert "tools.validation" not in start_source
    assert "tools.validation" not in server_source
    assert "cellertz.mcp" not in operator_source
    assert "from cellertz.mcp" not in operator_source
    assert "--persist" in operator_source
    assert "project_protein_design_run" in operator_source
