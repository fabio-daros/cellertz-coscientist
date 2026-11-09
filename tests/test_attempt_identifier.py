"""Attempt identifiers resolve before a tool may say evidence is absent."""

from __future__ import annotations

import json

from cellertz.application.attempt_coordinator import AttemptCoordinator
from cellertz.application.attempt_evidence import load_catalog, resolve_attempt


def _historical() -> dict:
    return load_catalog()


def test_exact_historical_id_takes_precedence_over_a_shared_suffix() -> None:
    item = _historical()["attempt.2LUTUOX4"]
    catalog = {"attempt.2LUTUOX4": item, "copy.2LUTUOX4": item}
    resolved = resolve_attempt(catalog, "attempt.2LUTUOX4", "copy.2LUTUOX4")
    assert resolved["status"] == "ok"
    assert resolved["requested_id"] == "attempt.2LUTUOX4"
    assert resolved["resolved_attempt_id"] == "attempt.2LUTUOX4"
    assert resolved["item"] is item


def test_unique_suffix_resolves_the_historical_attempt() -> None:
    catalog = _historical()
    resolved = resolve_attempt(catalog, "2LUTUOX4", None)
    assert resolved["status"] == "ok"
    assert resolved["requested_id"] == "2LUTUOX4"
    assert resolved["resolved_attempt_id"] == "attempt.2LUTUOX4"
    assert resolved["item"]["record"]["attempt_id"] == "attempt.2LUTUOX4"


def test_shared_suffix_asks_for_clarification_and_selects_nothing() -> None:
    item = _historical()["attempt.2LUTUOX4"]
    catalog = {"attempt.2LUTUOX4": item, "copy.2LUTUOX4": item}
    resolved = resolve_attempt(catalog, "2LUTUOX4", "attempt.2LUTUOX4")
    assert resolved["status"] == "ambiguous"
    assert resolved["requested_id"] == "2LUTUOX4"
    assert resolved["resolved_attempt_id"] is None
    assert resolved["matching_attempt_ids"] == ["attempt.2LUTUOX4", "copy.2LUTUOX4"]
    assert "item" not in resolved
    assert "ask which" in resolved["error"].casefold()


def test_unknown_id_lists_registered_ids_and_does_not_fall_back() -> None:
    catalog = _historical()
    resolved = resolve_attempt(catalog, "attempt.NOPE", "attempt.2LUTUOX4")
    assert resolved["status"] == "unknown_attempt"
    assert resolved["requested_id"] == "attempt.NOPE"
    assert resolved["resolved_attempt_id"] is None
    assert resolved["available_attempt_ids"] == ["attempt.2LUTUOX4"]
    assert "attempt.2LUTUOX4" in resolved["error"]
    assert "unavailable" in resolved["error"]
    assert "item" not in resolved


def test_bare_suffix_is_resolved_for_every_evidence_tool() -> None:
    def complete(messages, _tools, tool_choice="auto"):
        tool_messages = [item for item in messages if item.get("role") == "tool"]
        if not tool_messages:
            return {
                "content": "",
                "tool_calls": [
                    _call("list", "list_verified_attempts", {}),
                    _call("evidence", "get_attempt_evidence", {"attempt_id": "2LUTUOX4"}),
                    _call("metrics", "compare_model_metrics", {"attempt_id": "2LUTUOX4"}),
                    _call("contacts", "get_geometric_contacts", {"attempt_id": "2LUTUOX4"}),
                ],
            }
        by_id = {item["tool_call_id"]: json.loads(item["content"]) for item in tool_messages}
        for key in ("evidence", "metrics", "contacts"):
            assert by_id[key]["requested_id"] == "2LUTUOX4"
            assert by_id[key]["resolved_attempt_id"] == "attempt.2LUTUOX4"
            assert by_id[key]["status"] not in {"missing", "unknown_attempt"}
        cells = {row["metric"]: row for row in by_id["metrics"]["metrics"]}
        assert cells["n_InterfaceResidues"]["model1"]["text"] == "15"
        assert cells["n_InterfaceResidues"]["model2"]["kind"] == "recorded_zero"
        context = json.loads(messages[1]["content"].removeprefix("Conversation context: "))
        assert context["selected_attempt"] == "attempt.2LUTUOX4"
        assert tool_choice == "auto"
        return {
            "content": "attempt.2LUTUOX4 has 15 interface residues on model1 and a recorded zero on model2.",
            "tool_calls": [],
        }

    reply = AttemptCoordinator(_historical(), complete).ask("Analyze the historical PDL1 attempt.")
    assert reply["attempt_id"] == "attempt.2LUTUOX4"
    for step in reply["steps"][:-1]:
        if step["tool"] == "list_verified_attempts":
            continue
        assert step["requested_id"] == "2LUTUOX4"
        assert step["resolved_attempt_id"] == "attempt.2LUTUOX4"
        assert step["arguments"]["attempt_id"] == "2LUTUOX4"
    evidence = [item for item in reply["evidence"] if item["tool"] == "compare_model_metrics"]
    assert evidence[0]["requested_id"] == "2LUTUOX4"
    assert evidence[0]["resolved_attempt_id"] == "attempt.2LUTUOX4"


def test_a_later_call_keeps_the_canonical_id() -> None:
    calls = {"n": 0}

    def complete(messages, _tools, _tool_choice="auto"):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "content": "",
                "tool_calls": [_call("evidence", "get_attempt_evidence", {"attempt_id": "2LUTUOX4"})],
            }
        if calls["n"] == 2:
            return {"content": "The registered id is attempt.2LUTUOX4.", "tool_calls": []}
        context = json.loads(messages[1]["content"].removeprefix("Conversation context: "))
        assert context["selected_attempt"] == "attempt.2LUTUOX4"
        if calls["n"] == 3:
            return {"content": "", "tool_calls": [_call("again", "compare_model_metrics", {})]}
        return _resolved_follow_up(messages)

    coordinator = AttemptCoordinator(_historical(), complete)
    first = coordinator.ask("Analyze the historical PDL1 attempt.")
    second = coordinator.ask("Compare the recorded metrics.", first["conversation_id"])
    assert first["attempt_id"] == "attempt.2LUTUOX4"
    assert second["attempt_id"] == "attempt.2LUTUOX4"
    assert second["steps"][0]["requested_id"] is None
    assert second["steps"][0]["resolved_attempt_id"] == "attempt.2LUTUOX4"


def _resolved_follow_up(messages: list) -> dict:
    payload = json.loads(messages[-1]["content"])
    assert payload["resolved_attempt_id"] == "attempt.2LUTUOX4"
    assert payload["requested_id"] is None
    cells = {row["metric"]: row for row in payload["metrics"]}
    assert cells["pLDDT"]["model1"]["text"] == "0.83"
    return {"content": "model1 global pLDDT is 0.83.", "tool_calls": []}


def test_unknown_identifier_is_reported_before_an_absence_conclusion() -> None:
    calls = {"n": 0}

    def complete(messages, _tools, tool_choice="auto"):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "content": "",
                "tool_calls": [_call("bad", "get_attempt_evidence", {"attempt_id": "attempt.NOPE"})],
            }
        if calls["n"] == 2:
            assert tool_choice == "required"
            payload = json.loads(messages[-1]["content"])
            assert payload["status"] == "unknown_attempt"
            assert payload["resolved_attempt_id"] is None
            assert payload["available_attempt_ids"] == ["attempt.2LUTUOX4"]
            assert "pdb_verified" not in payload
            return {"content": "The evidence is unavailable.", "tool_calls": []}
        assert tool_choice == "auto"
        assert "do not conclude that the evidence is unavailable" in messages[-1]["content"].casefold()
        assert "do not select a different attempt" in messages[-1]["content"].casefold()
        return {
            "content": (
                "get_attempt_evidence rejected attempt.NOPE. "
                "The available identifier is attempt.2LUTUOX4."
            ),
            "tool_calls": [],
        }

    reply = AttemptCoordinator(_historical(), complete).ask("Analyze the historical PDL1 attempt.")
    assert reply["status"] == "answered"
    assert reply["attempt_id"] is None
    assert reply["text"] == (
        "get_attempt_evidence rejected attempt.NOPE. The available identifier is attempt.2LUTUOX4."
    )
    assert "unavailable" not in reply["text"].casefold()
    assert reply["steps"][0]["status"] == "unknown_attempt"
    assert reply["steps"][0]["requested_id"] == "attempt.NOPE"
    assert reply["steps"][0]["resolved_attempt_id"] is None
    assert reply["evidence"][0]["resolved_attempt_id"] is None


def _call(call_id: str, name: str, arguments: dict) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }
