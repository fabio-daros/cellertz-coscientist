import inspect
import sys
from pathlib import Path

from cellertz.mcp.capabilities import cellertz_capabilities
from cellertz.mcp.run_state import protein_get_run_state, set_run_state_reader
from cellertz.mcp.server import cellertz_get_capabilities
from cellertz.mcp.server import protein_get_run_state as mcp_protein_get_run_state
from cellertz.state.run_state import (
    FileProteinRunStateStore,
    ProteinRunState,
    ProteinRunStageState,
    canonical_run_state_json,
)


FORBIDDEN_SUPERVISOR_TERMS = (
    "ProteinSupervisor",
    ".start(",
    ".retry(",
    ".resume(",
    ".generate(",
    ".score(",
)

STAGE_COMPLETE = ProteinRunState(
    run_id="protein_run_001",
    found=True,
    generation=ProteinRunStageState(status="complete"),
    scoring=ProteinRunStageState(status="complete", missing_ipsae=0),
    ranking=ProteinRunStageState(status="complete"),
    critique=ProteinRunStageState(status="complete"),
    prioritization=ProteinRunStageState(status="complete"),
)


def _write_state(store: FileProteinRunStateStore, state: ProteinRunState) -> Path:
    store.put_run_state(state)
    path = store.state_path(state.run_id)
    assert path is not None
    return path


def test_existing_run_returns_operational_state(tmp_path):
    store = FileProteinRunStateStore(tmp_path)
    _write_state(store, STAGE_COMPLETE)
    set_run_state_reader(store)

    payload = protein_get_run_state("protein_run_001")

    assert payload == {
        "found": True,
        "run_id": "protein_run_001",
        "generation": {"status": "complete"},
        "scoring": {"status": "complete", "missing_ipsae": 0},
        "ranking": {"status": "complete"},
        "critique": {"status": "complete"},
        "prioritization": {"status": "complete"},
    }
    assert mcp_protein_get_run_state("protein_run_001") == payload


def test_missing_run_does_not_create_anything(tmp_path):
    store = FileProteinRunStateStore(tmp_path)
    set_run_state_reader(store)

    payload = protein_get_run_state("protein_run_missing")

    assert payload == {"found": False, "run_id": "protein_run_missing"}
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []
    assert store.state_path("protein_run_missing") == tmp_path / "protein_run_missing" / "state.json"
    assert not (tmp_path / "protein_run_missing").exists()


def test_read_does_not_change_bytes_or_mtime(tmp_path):
    store = FileProteinRunStateStore(tmp_path)
    path = _write_state(store, STAGE_COMPLETE)
    set_run_state_reader(store)
    before = path.read_bytes()
    mtime = path.stat().st_mtime_ns

    protein_get_run_state("protein_run_001")

    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == mtime


def test_failed_and_pending_and_error_are_returned_as_stored(tmp_path):
    store = FileProteinRunStateStore(tmp_path)
    state = ProteinRunState(
        run_id="protein_run_partial",
        found=True,
        generation=ProteinRunStageState(status="complete"),
        scoring=ProteinRunStageState(status="failed", error="Boltz2 unavailable"),
        ranking=ProteinRunStageState(status="pending"),
        critique=ProteinRunStageState(status="pending"),
        prioritization=ProteinRunStageState(status="pending"),
    )
    _write_state(store, state)
    set_run_state_reader(store)

    payload = protein_get_run_state("protein_run_partial")

    assert payload["scoring"] == {
        "status": "failed",
        "error": "Boltz2 unavailable",
    }
    assert payload["ranking"] == {"status": "pending"}
    assert payload["found"] is True


def test_mcp_package_does_not_import_or_call_supervisor():
    sources = []
    for name, module in list(sys.modules.items()):
        if name.startswith("cellertz.mcp") and module is not None:
            try:
                sources.append(inspect.getsource(module))
            except (OSError, TypeError):
                continue
    joined = "\n".join(sources)
    assert "protein_supervisor" not in joined
    assert "domains.protein_design.agents.supervisor" not in joined
    for term in FORBIDDEN_SUPERVISOR_TERMS:
        assert term not in joined


def test_tool_stays_read_only_and_capabilities_still_work(tmp_path):
    store = FileProteinRunStateStore(tmp_path)
    _write_state(store, STAGE_COMPLETE)
    set_run_state_reader(store)

    assert cellertz_get_capabilities() == cellertz_capabilities()
    assert "mutating_operations" not in cellertz_capabilities()
    assert cellertz_capabilities()["mutation"]["real_generators_allowed"] is False
    payload = protein_get_run_state("protein_run_001")
    assert payload["found"] is True
    assert "candidates" not in payload
    assert "ranking_score" not in str(payload)


def test_state_before_equals_state_after_byte_for_byte(tmp_path):
    store = FileProteinRunStateStore(tmp_path)
    path = _write_state(store, STAGE_COMPLETE)
    before = path.read_bytes()
    expected = canonical_run_state_json(STAGE_COMPLETE.to_document()).encode()
    assert before == expected
    set_run_state_reader(store)

    protein_get_run_state("protein_run_001")
    protein_get_run_state("protein_run_missing")

    assert path.read_bytes() == before == expected
