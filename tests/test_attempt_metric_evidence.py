"""Metric names and evidence references from the packaged historical review."""

from __future__ import annotations

import json

from cellertz.application.attempt_coordinator import AttemptCoordinator
from cellertz.application.attempt_evidence import compare_metrics, load_catalog

_REVIEWED = (
    ("pLDDT", "0.83", "0.89", "recorded"),
    ("Binder_pLDDT", "0.83", "0.61", "recorded"),
    ("i_pTM", "0.16", "0.05", "recorded"),
    ("n_InterfaceResidues", "15", "0", "recorded_zero"),
)


def test_packaged_review_keeps_recorded_zero_and_empty_cells() -> None:
    item = load_catalog()["attempt.2LUTUOX4"]
    recorded = {row["metric"]: row for row in item["record"]["metrics"]}
    for metric, model1, model2, model2_kind in _REVIEWED:
        assert recorded[metric]["model1"]["text"] == model1
        assert recorded[metric]["model1"]["kind"] == "recorded"
        assert recorded[metric]["model2"]["text"] == model2
        assert recorded[metric]["model2"]["kind"] == model2_kind
    blank = recorded["InterfaceHbondsPercentage"]["model2"]
    assert blank["kind"] == "empty_field"
    assert "text" not in blank
    assert item["record"]["interface_residues"]["kind"] == "empty_field"


def test_unsupported_metric_names_are_rejected_without_declaring_absence() -> None:
    item = load_catalog()["attempt.2LUTUOX4"]
    rejected = compare_metrics(item, ["rmsd", "interface_scores"])
    assert rejected["status"] == "unsupported_metrics"
    assert rejected["rejected_metrics"] == ["rmsd", "interface_scores"]
    assert rejected["metrics"] == []
    assert rejected["absence_established"] is False
    assert "n_InterfaceResidues" in rejected["valid_metrics"]
    assert {"Hotspot_RMSD", "Target_RMSD", "Binder_RMSD"} <= set(rejected["valid_metrics"])
    assert "does not mean the measurement is absent" in rejected["error"]
    assert "unavailable" not in rejected["error"]
    found = compare_metrics(
        item,
        ["pLDDT", "Binder_pLDDT", "i_pTM", "n_InterfaceResidues", "InterfaceHbondsPercentage"],
    )
    assert found["status"] == "ok"
    returned = {row["metric"]: row for row in found["metrics"]}
    recorded = {row["metric"]: row for row in item["record"]["metrics"]}
    for metric, *_rest in _REVIEWED:
        assert returned[metric]["model1"] == recorded[metric]["model1"]
        assert returned[metric]["model2"] == recorded[metric]["model2"]
    assert returned["InterfaceHbondsPercentage"]["model2"] == {"kind": "empty_field"}


def test_a_rejected_metric_call_remains_in_the_trace() -> None:
    catalog = load_catalog()

    def complete(messages, tools, _choice):
        schema = next(tool for tool in tools if tool["function"]["name"] == "compare_model_metrics")
        identifiers = schema["function"]["parameters"]["properties"]["metrics"]["items"]["enum"]
        assert "n_InterfaceResidues" in identifiers
        assert "rmsd" not in identifiers
        assert "interface_scores" not in identifiers
        tool_messages = [item for item in messages if item.get("role") == "tool"]
        if not tool_messages:
            return {
                "tool_calls": [
                    {
                        "id": "metrics",
                        "function": {
                            "name": "compare_model_metrics",
                            "arguments": json.dumps(
                                {
                                    "attempt_id": "attempt.2LUTUOX4",
                                    "metrics": ["rmsd", "interface_scores"],
                                }
                            ),
                        },
                    }
                ]
            }
        payload = json.loads(tool_messages[-1]["content"])
        assert payload["status"] == "unsupported_metrics"
        assert payload["absence_established"] is False
        return {
            "content": (
                "compare_model_metrics rejected rmsd and interface_scores. "
                "The error returned the valid identifiers."
            )
        }

    reply = AttemptCoordinator(catalog, complete).ask(
        "Analyze the historical PDL1 attempt. Do not propose or launch generation."
    )
    step = reply["steps"][0]
    assert step["tool"] == "compare_model_metrics"
    assert step["status"] == "unsupported_metrics"
    assert step["arguments"] == {
        "attempt_id": "attempt.2LUTUOX4",
        "metrics": ["rmsd", "interface_scores"],
    }
    assert reply["evidence"][0]["status"] == "unsupported_metrics"
    assert "n_InterfaceResidues" in reply["evidence"][0]["references"][0]["valid_metrics"]
    assert "unavailable" not in reply["text"]
