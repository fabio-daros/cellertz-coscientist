"""Coordinator traces are saved per attempt and are not reconstructed."""

from __future__ import annotations

import json
from pathlib import Path

from cellertz.application.apptainer_demonstration import recover_completed_attempt
from cellertz.application.attempt_coordinator import AttemptCoordinator
from cellertz.application.attempt_evidence import load_catalog
from cellertz.application.attempt_inspection import absent_trace_note, inspect_attempt
from cellertz.application.coordinator_trace import (
    SQLiteCoordinatorTraceStore,
    open_coordinator_trace_store,
    trace_document,
)
from tests.test_bindcraft_output_ingestion import _ATTEMPT, _DESIGN, _build_attempt

_HISTORICAL = "attempt.2LUTUOX4"


def test_a_saved_trace_stays_on_its_attempt(tmp_path: Path, monkeypatch) -> None:
    _publish(tmp_path, monkeypatch)
    catalog = load_catalog()
    calls = {"n": 0}

    def complete(messages: list[dict], _tools: list[dict], _choice: str = "auto") -> dict:
        calls["n"] += 1
        if calls["n"] in {1, 3}:
            attempt_id = _ATTEMPT if calls["n"] == 1 else _HISTORICAL
            return _tool("get_attempt_evidence", {"attempt_id": attempt_id})
        return {"content": "The recorded evidence is above.", "tool_calls": []}

    coordinator = AttemptCoordinator(catalog, complete)
    first = coordinator.ask("What does the recovered attempt show?", "conv-recovered")
    second = coordinator.ask("What does the historical attempt show?", "conv-historical")
    assert first["attempt_id"] == _ATTEMPT
    assert second["attempt_id"] == _HISTORICAL
    recovered = coordinator.traces_for(_ATTEMPT)
    historical = coordinator.traces_for(_HISTORICAL)
    assert len(recovered) == 1
    assert len(historical) == 1
    assert recovered[0]["source_query"] == "What does the recovered attempt show?"
    assert recovered[0]["execution_record"] is None
    assert recovered[0]["agent_executed"] is False
    kinds = [step["execution"] for step in recovered[0]["steps"]]
    assert kinds == ["deterministic_tool", "language_model_synthesis"]
    assert recovered[0]["steps"][0]["tool"] == "get_attempt_evidence"
    assert recovered[0]["steps"][0]["specialist_role"] == "ranking"
    assert recovered[0]["steps"][0]["resolved_attempt_id"] == _ATTEMPT
    assert recovered[0]["steps"][0]["agent_executed"] is False
    assert all(step["agent_executed"] is False for step in historical[0]["steps"])
    status, payload = inspect_attempt(
        catalog,
        _ATTEMPT,
        {"candidate": _DESIGN, "model": "model1"},
        traces_for=coordinator.traces_for,
    )
    assert status == 200
    assert payload["coordinator_trace_note"] is None
    assert payload["execution_constraints"]["unmet"] is True
    assert payload["models"]["model2"]["recorded_contacts"]["residue_pairs"] is None
    shown = payload["coordinator_traces"]
    assert len(shown) == 1
    assert _HISTORICAL not in json.dumps(shown)
    hrefs = _hrefs(shown[0])
    assert f"/api/attempts/{_ATTEMPT}/structures/model1.pdb" in hrefs
    assert f"/api/attempts/{_ATTEMPT}/structures/model2.pdb" in hrefs
    bare_status, bare = inspect_attempt(
        catalog, _ATTEMPT, {"candidate": _DESIGN, "model": "model1"}
    )
    assert bare_status == 200
    assert bare["coordinator_trace_note"] == "No recorded coordinator trace"


def test_an_attempt_without_a_question_has_no_trace(tmp_path: Path, monkeypatch) -> None:
    _publish(tmp_path, monkeypatch)
    catalog = load_catalog()
    _status, payload = inspect_attempt(
        catalog, _ATTEMPT, {"candidate": _DESIGN, "model": "model1"}
    )
    assert payload["coordinator_traces"] == []
    assert payload["coordinator_trace_note"] == absent_trace_note()
    assert payload["coordinator_trace_note"] == "No recorded coordinator trace"
    assert payload["execution_constraints"]["unmet"] is True


def test_sqlite_keeps_one_attempt_and_does_not_invent_history(tmp_path: Path) -> None:
    path = tmp_path / "traces.sqlite"
    store = SQLiteCoordinatorTraceStore(path)
    store.append(
        trace_document(
            "Show the recovered models.",
            {"status": "answered", "attempt_id": _ATTEMPT, "conversation_id": "conv-1"},
            [
                {
                    "specialist": "scoring",
                    "mode": "deterministic",
                    "tool": "compare_model_metrics",
                    "status": "ok",
                    "requested_id": _ATTEMPT,
                    "resolved_attempt_id": _ATTEMPT,
                    "summary": "Returns recorded metric cells.",
                    "evidence_references": [],
                }
            ],
        )
    )
    store.close()
    reopened = SQLiteCoordinatorTraceStore(path)
    saved = reopened.for_attempt(_ATTEMPT)
    assert saved[0]["source_query"] == "Show the recovered models."
    assert saved[0]["steps"][0]["execution"] == "deterministic_tool"
    assert reopened.for_attempt(_HISTORICAL) == []
    reopened.close()
    fresh = open_coordinator_trace_store(
        {
            "CELLERTZ_COORDINATOR_TRACE_STORE": "sqlite",
            "CELLERTZ_COORDINATOR_TRACE_DB": str(tmp_path / "empty.sqlite"),
        }
    )
    assert fresh.for_attempt(_ATTEMPT) == []
    fresh.close()


def _publish(tmp_path: Path, monkeypatch) -> None:
    attempt = _build_attempt(tmp_path)
    assert recover_completed_attempt(attempt)["status"] == "verified"
    monkeypatch.setenv("CELLERTZ_ATTEMPT_CATALOG", str(tmp_path))


def _tool(name: str, arguments: dict) -> dict:
    return {
        "content": "",
        "tool_calls": [
            {
                "id": f"call_{name}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def _hrefs(trace: dict) -> set[str]:
    found: set[str] = set()
    for step in trace["steps"]:
        for reference in step["evidence_references"]:
            models = reference.get("models") or {}
            for model in models.values():
                href = model.get("structure_href")
                if isinstance(href, str):
                    found.add(href)
    return found
