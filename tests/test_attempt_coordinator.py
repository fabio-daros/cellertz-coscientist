"""Coordinator tool loop. The fake model is not a keyword matcher."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import cast

from cellertz.application.attempt_coordinator import AttemptCoordinator, _complete_with
from cellertz.application.attempt_evidence import (
    AttemptFiles,
    geometric_contacts,
    load_catalog,
    workspace_root,
)
from cellertz.application.conversation import ReadOnlyConversation
from cellertz.application.conversation_transport import configured_handler
from cellertz.conversation.model import ChatCompletionModel

_EVIDENCE = {
    "id": "call_evidence",
    "type": "function",
    "function": {"name": "get_attempt_evidence", "arguments": "{}"},
}


def _catalog() -> dict:
    root = workspace_root()
    return load_catalog(
        (
            AttemptFiles(
                review_json=root / "src" / "manual-attempt-2LUTUOX4.json",
                pdb_dir=root / "public" / "manual-review" / "attempt.2LUTUOX4",
            ),
        )
    )


def test_real_pdb_contacts_match_the_verified_review() -> None:
    catalog = _catalog()
    item = catalog["attempt.2LUTUOX4"]
    assert item["pdb_verified"] is True
    contacts = geometric_contacts(item)
    assert contacts["status"] == "calculated"
    assert contacts["models"]["model1"]["calculated"]["residue_pairs"] == 37
    assert contacts["models"]["model2"]["calculated"]["residue_pairs"] == 0
    assert contacts["models"]["model1"]["matches_recorded_counts"] is True
    assert contacts["models"]["model2"]["matches_recorded_counts"] is True


def test_question_reaches_a_tool_then_an_english_answer() -> None:
    seen: list[list[dict]] = []

    def complete(messages: list[dict], tools: list[dict], tool_choice: str = "auto") -> dict:
        seen.append(messages)
        assert "Reply in English" in messages[0]["content"]
        assert tool_choice == ("required" if len(seen) == 1 else "auto")
        assert any(tool["function"]["name"] == "get_geometric_contacts" for tool in tools)
        if len(seen) == 1:
            return {"content": "", "tool_calls": [_EVIDENCE]}
        tool_payload = json.loads(messages[-1]["content"])
        assert tool_payload["representative"]["model"] == "model2"
        assert tool_payload["ranking_score"] == "not_recorded"
        return {
            "content": "model2 was selected by global pLDDT, not because it has the stronger interface.",
            "tool_calls": [],
        }

    reply = AttemptCoordinator(_catalog(), complete).ask("Why was model 2 selected?")
    assert reply["status"] == "answered"
    assert reply["attempt_id"] == "attempt.2LUTUOX4"
    assert reply["execution_connected"] is False
    assert [step["mode"] for step in reply["steps"]] == ["deterministic", "language_model"]
    assert reply["steps"][0]["specialist"] == "ranking"
    assert "fixture" not in reply["text"].casefold()


def test_a_catalog_list_is_not_accepted_as_the_conclusion() -> None:
    calls = {"n": 0}

    def complete(messages: list[dict], _tools: list[dict], tool_choice: str = "auto") -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            assert tool_choice == "required"
            return {
                "content": "model2 was selected because it is the only design.",
                "tool_calls": [
                    {
                        "id": "call_list",
                        "type": "function",
                        "function": {"name": "list_verified_attempts", "arguments": "{}"},
                    }
                ],
            }
        if calls["n"] == 2:
            assert tool_choice == "required"
            assert json.loads(messages[-1]["content"])["attempts"][0]["attempt_id"] == "attempt.2LUTUOX4"
            return {"content": "", "tool_calls": [_EVIDENCE]}
        assert tool_choice == "auto"
        assert json.loads(messages[-1]["content"])["representative"]["rule"] == "highest pLDDT"
        return {
            "content": "model2 was selected by the highest global pLDDT.",
            "tool_calls": [],
        }

    reply = AttemptCoordinator(_catalog(), complete).ask("Why was model 2 selected?")
    assert reply["text"] == "model2 was selected by the highest global pLDDT."
    assert reply["attempt_id"] == "attempt.2LUTUOX4"
    assert [step["tool"] for step in reply["steps"]] == [
        "list_verified_attempts",
        "get_attempt_evidence",
        None,
    ]


def test_missing_execution_constraints_are_not_treated_as_compliance() -> None:
    calls = {"n": 0}

    def complete(messages: list[dict], _tools: list[dict], tool_choice: str = "auto") -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            assert tool_choice == "required"
            return {"content": "", "tool_calls": [_EVIDENCE]}
        payload = json.loads(messages[-1]["content"])
        assert payload["execution_constraints"] is None
        assert payload["execution_constraints_reported"] is False
        assert payload["absence_of_execution_constraints_is_not_compliance"] is True
        assert payload["execution_compliance_established"] is False
        return {
            "content": "No unmet trajectory execution constraints are reported.",
            "tool_calls": [],
        }

    reply = AttemptCoordinator(_catalog(), complete).ask("Were the trajectory limits met?")
    assert "no unmet" not in reply["text"].casefold()
    assert "does not report execution constraints" in reply["text"]
    assert "That absence is not compliance." in reply["text"]
    assert "separate from execution compliance and scientific validation" in reply["text"]


def test_an_answer_without_evidence_is_sent_back_for_a_tool() -> None:
    calls = {"n": 0}

    def complete(messages: list[dict], _tools: list[dict], tool_choice: str = "auto") -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"content": "model2 was selected because it is the only design.", "tool_calls": []}
        if calls["n"] == 2:
            assert "catalog list is not evidence" in messages[-1]["content"]
            assert tool_choice == "required"
            return {"content": "", "tool_calls": [_EVIDENCE]}
        return {
            "content": "model2 was selected by the highest global pLDDT.",
            "tool_calls": [],
        }

    reply = AttemptCoordinator(_catalog(), complete).ask("Why was model 2 selected?")
    assert "only design" not in reply["text"]
    assert "highest global pLDDT" in reply["text"]


def test_follow_up_keeps_the_previous_conclusion() -> None:
    calls = {"n": 0}

    def complete(messages: list[dict], _tools: list[dict], _tool_choice: str = "auto") -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"content": "", "tool_calls": [_EVIDENCE]}
        if calls["n"] == 2:
            return {"content": "model2 has no 5 angstrom contacts.", "tool_calls": []}
        assert any("model2 has no 5 angstrom contacts." in str(item.get("content")) for item in messages)
        assert json.loads(messages[1]["content"].removeprefix("Conversation context: "))[
            "selected_attempt"
        ] == "attempt.2LUTUOX4"
        return {"content": "The earlier conclusion still refers to model2.", "tool_calls": []}

    coordinator = AttemptCoordinator(_catalog(), complete)
    first = coordinator.ask("Which model has stronger evidence of an interface?")
    second = coordinator.ask("What about the model we just discussed?", first["conversation_id"])
    assert second["status"] == "answered"
    assert second["attempt_id"] == "attempt.2LUTUOX4"


def test_unconfigured_model_does_not_answer_from_a_fixture() -> None:
    reply = AttemptCoordinator(_catalog(), None).ask("Analyze the available PDL1 attempt.")
    assert reply["status"] == "provider_unavailable"
    assert reply["steps"] == []
    assert "fixture" in reply["text"].casefold()
    assert "PDL1_l65" not in reply["text"]


def test_provider_budget_keeps_the_tool_trace_and_skips_a_fixture() -> None:
    def complete(_messages: list[dict], _tools: list[dict], _tool_choice: str = "auto") -> dict:
        raise RuntimeError("provider_budget")

    reply = AttemptCoordinator(_catalog(), complete).ask("What should we investigate next?")
    assert reply["status"] == "provider_budget"
    assert "fixture" in reply["text"].casefold()
    assert reply["steps"] == []


def test_generation_request_is_a_plan_and_not_a_launch() -> None:
    def complete(messages: list[dict], _tools: list[dict], _tool_choice: str = "auto") -> dict:
        if not any(item.get("role") == "tool" for item in messages):
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_plan",
                        "type": "function",
                        "function": {
                            "name": "propose_generation_plan",
                            "arguments": json.dumps({"request_text": "Generate another binder."}),
                        },
                    }
                ],
            }
        payload = json.loads(messages[-1]["content"])
        assert payload["approved"] is False
        assert payload["execution_connected"] is False
        return {
            "content": "Execution is not connected. The plan is not approved and BindCraft was not launched.",
            "tool_calls": [],
        }

    reply = AttemptCoordinator(_catalog(), complete).ask("Generate another binder.")
    assert reply["steps"][0]["specialist"] == "generation"
    assert reply["steps"][0]["mode"] == "deterministic"
    assert "not approved" in reply["text"]


def test_hash_mismatch_refuses_contact_calculation(tmp_path: Path) -> None:
    review = {
        "attempt_id": "attempt.bad",
        "design": "bad",
        "filter_policy": "no_filters",
        "scientific_evaluation": "pending",
        "platform_scientific_execution": "disabled",
        "platform_ipsae_ranking": False,
        "experimental_validation": False,
        "representative": {"model": "model2"},
        "chains": {"target": "A", "binder": "B"},
        "interface_residues": {"kind": "empty_field"},
        "blank_model2_metrics": [],
        "blank_model2_note": "",
        "absent_models": [],
        "provenance": {},
        "metrics": [],
        "models": {
            "model1": {
                "sha256": "0" * 64,
                "representative": False,
                "pdb_url": "/model1.pdb",
                "contacts": {
                    "binder_residues_in_contact": 0,
                    "target_residues_in_contact": 0,
                    "residue_pairs": 0,
                    "minimum_heavy_atom_distance_angstrom": 9.0,
                },
            },
            "model2": {
                "sha256": "1" * 64,
                "representative": True,
                "pdb_url": "/model2.pdb",
                "contacts": {
                    "binder_residues_in_contact": 0,
                    "target_residues_in_contact": 0,
                    "residue_pairs": 0,
                    "minimum_heavy_atom_distance_angstrom": 9.0,
                },
            },
        },
    }
    directory = tmp_path / "attempt.bad"
    directory.mkdir()
    (directory / "review.json").write_text(json.dumps(review), encoding="utf-8")
    (directory / "model1.pdb").write_text("ATOM\n", encoding="utf-8")
    (directory / "model2.pdb").write_text("ATOM\n", encoding="utf-8")
    catalog = load_catalog((AttemptFiles(directory / "review.json", directory),))

    def complete(messages: list[dict], _tools: list[dict], _tool_choice: str = "auto") -> dict:
        if not any(item.get("role") == "tool" for item in messages):
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_contacts",
                        "type": "function",
                        "function": {"name": "get_geometric_contacts", "arguments": "{}"},
                    }
                ],
            }
        payload = json.loads(messages[-1]["content"])
        assert payload["status"] == "hash_mismatch"
        assert payload["contacts"] is None
        return {"content": "The PDB hash does not match, so no contacts were calculated.", "tool_calls": []}

    reply = AttemptCoordinator(catalog, complete).ask("Which model has stronger evidence of an interface?")
    assert reply["steps"][0]["summary"].startswith("Refused")


def test_two_attempts_ask_for_clarification_instead_of_guessing() -> None:
    one = _catalog()["attempt.2LUTUOX4"]
    catalog = {"attempt.2LUTUOX4": one, "attempt.other": one}

    def complete(messages: list[dict], _tools: list[dict], _tool_choice: str = "auto") -> dict:
        if not any(item.get("role") == "tool" for item in messages):
            return {"content": "", "tool_calls": [_EVIDENCE]}
        payload = json.loads(messages[-1]["content"])
        assert payload["status"] == "ambiguous"
        return {"content": "Which verified attempt should I analyze?", "tool_calls": []}

    reply = AttemptCoordinator(catalog, complete).ask("Analyze the available attempt.")
    assert reply["attempt_id"] is None
    assert "Which verified attempt" in reply["text"]


def test_http_path_does_not_substitute_a_fixture_when_the_model_is_absent() -> None:
    handler = configured_handler(
        cast(ReadOnlyConversation, object()),
        coordinator=AttemptCoordinator(_catalog(), None),
    )
    body = json.dumps({"question": "Analyze the available PDL1 attempt."}).encode()
    instance = handler.__new__(handler)
    instance.path = "/api/attempt-coordinator"
    instance.headers = {"Content-Length": str(len(body))}
    instance.rfile = BytesIO(body)
    instance.wfile = BytesIO()
    instance.send_response = lambda code: setattr(instance, "status_code", code)
    instance.send_header = lambda *_args: None
    instance.end_headers = lambda: None
    instance.do_POST()
    payload = json.loads(instance.wfile.getvalue())
    assert instance.status_code == 200
    assert payload["status"] == "provider_unavailable"
    assert payload["execution_connected"] is False
    assert "PDL1_l65" not in payload["text"]


def test_tool_request_keeps_the_credential_out_of_the_body() -> None:
    seen: dict[str, object] = {}

    def transport(*, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        seen["url"] = url
        seen["body"] = json.loads(body)
        seen["authorization"] = headers["authorization"]
        return 200, json.dumps(
            {"choices": [{"message": {"content": "Done.", "tool_calls": [], "reasoning": "private"}}]}
        ).encode()

    model = ChatCompletionModel(
        provider="openrouter",
        model="test-model",
        api_key="secret-key",
        url="https://openrouter.ai/api/v1/chat/completions",
        transport=transport,
    )
    message = _complete_with(model)([{"role": "user", "content": "hello"}], [{"type": "function"}], "required")
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["tools"] == [{"type": "function"}]
    assert body["tool_choice"] == "required"
    assert "secret-key" not in json.dumps(body)
    assert seen["authorization"] == "Bearer secret-key"
    assert message == {"content": "Done.", "tool_calls": []}
