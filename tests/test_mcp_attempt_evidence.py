import asyncio
import json

import pytest

from cellertz.application.attempt_evidence import (
    compare_metrics,
    default_attempt_files,
    evidence_payload,
    load_catalog,
)
from cellertz.mcp.attempt_evidence import (
    compare_model_metrics,
    get_attempt_evidence,
    list_verified_attempts,
    set_attempt_catalog,
)
from cellertz.mcp.server import mcp

ATTEMPT = "attempt.2LUTUOX4"


@pytest.fixture(autouse=True)
def packaged_catalog(monkeypatch):
    monkeypatch.delenv("CELLERTZ_ATTEMPT_CATALOG", raising=False)
    set_attempt_catalog(None)
    yield
    set_attempt_catalog(None)


def _call(name: str, arguments: dict) -> dict:
    result = asyncio.run(mcp.call_tool(name, arguments))
    assert result.is_error is False
    return json.loads(result.content[0].text)


def test_tools_are_registered_with_their_arguments():
    tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    assert set(tools["list_verified_attempts"].input_schema.get("properties", {})) == set()
    assert set(tools["get_attempt_evidence"].input_schema["properties"]) == {"attempt_id"}
    assert set(tools["compare_model_metrics"].input_schema["properties"]) == {
        "attempt_id",
        "metrics",
    }


def test_list_returns_verified_attempts_without_ranking():
    payload = _call("list_verified_attempts", {})
    assert payload == list_verified_attempts()
    assert [item["attempt_id"] for item in payload["attempts"]] == [ATTEMPT]
    assert payload["attempts"][0]["pdb_verified"] is True
    assert "ranking_score" not in payload["attempts"][0]


def test_evidence_matches_the_application_payload_and_resolves_short_ids():
    item = load_catalog()[ATTEMPT]
    payload = _call("get_attempt_evidence", {"attempt_id": "2LUTUOX4"})
    expected = evidence_payload(ATTEMPT, item)
    assert {key: payload[key] for key in expected} == json.loads(json.dumps(expected))
    assert payload["requested_id"] == "2LUTUOX4"
    assert payload["resolved_attempt_id"] == ATTEMPT
    assert payload["status"] == "ok"
    assert payload["launched_by_conversation"] is False


def test_unknown_attempt_is_rejected_without_selecting_another():
    payload = get_attempt_evidence("attempt.NOPE")
    assert payload["status"] == "unknown_attempt"
    assert payload["resolved_attempt_id"] is None
    assert payload["available_attempt_ids"] == [ATTEMPT]
    assert "item" not in payload
    assert _call("compare_model_metrics", {"attempt_id": "NOPE"})["status"] == "unknown_attempt"


def test_metrics_return_recorded_cells_and_reject_unknown_names():
    item = load_catalog()[ATTEMPT]
    every = _call("compare_model_metrics", {"attempt_id": ATTEMPT})
    assert every["metrics"] == compare_metrics(item)["metrics"]

    chosen = _call("compare_model_metrics", {"attempt_id": ATTEMPT, "metrics": ["n_InterfaceResidues"]})
    assert [row["metric"] for row in chosen["metrics"]] == ["n_InterfaceResidues"]
    assert chosen["metrics"][0]["model2"]["kind"] == "recorded_zero"

    rejected = compare_model_metrics(ATTEMPT, ["rmsd"])
    assert rejected["status"] == "unsupported_metrics"
    assert rejected["rejected_metrics"] == ["rmsd"]
    assert rejected["absence_established"] is False


def test_reads_do_not_change_review_or_pdb_bytes():
    files = default_attempt_files()[0]
    paths = [files.review_json, *sorted(files.pdb_dir.glob("*.pdb"))]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}

    list_verified_attempts()
    get_attempt_evidence(ATTEMPT)
    compare_model_metrics(ATTEMPT)

    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths} == before
