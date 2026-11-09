"""Coordinator harness without an external provider."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cellertz.application.attempt_coordinator import (
    AttemptCoordinator,
    _complete_with,
    handle_attempt_coordinator_read,
)
from cellertz.application.attempt_evidence import AttemptFiles, load_catalog, workspace_root
from cellertz.application.coordinator_harness import (
    VALIDATION_LIMITATIONS,
    CoordinatorLimits,
    CoordinatorLimitsError,
    resolve_coordinator_limits,
)
from cellertz.application.coordinator_trace import SQLiteCoordinatorTraceStore
from cellertz.conversation.model import ChatCompletionModel

_REQUEST = "request_demo1"
_REVISION = 2
_ANSWER = (
    "model2 was selected by global pLDDT, not because it has the stronger interface. "
    "ranking_score is not_recorded."
)
_EVIDENCE = {
    "id": "call_evidence",
    "type": "function",
    "function": {"name": "get_attempt_evidence", "arguments": "{}"},
}


def test_an_allowed_python_tool_can_be_accepted() -> None:
    calls = {"n": 0}

    def complete(messages: list[dict], _tools: list[dict], _tool_choice: str = "auto") -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"content": "", "tool_calls": [_EVIDENCE], "usage": {"total_tokens": 4}}
        assert json.loads(messages[-1]["content"])["ranking_score"] == "not_recorded"
        return {"content": _ANSWER, "tool_calls": []}

    reply = AttemptCoordinator(_catalog(), complete).ask("Why was model 2 selected?", None, _REQUEST, _REVISION)
    assert reply["status"] == "answered"
    assert reply["conclusion_accepted"] is True
    assert reply["steps"][0]["tool"] == "get_attempt_evidence"
    assert reply["steps"][0]["status"] == "ok"
    assert reply["execution_connected"] is False
    assert reply["limits"]["max_output_tokens_per_call"] == 1500
    assert reply["limits"]["output_token_limit_is_per_call"] is True
    assert reply["limits"]["token_budget"] is None
    assert reply["limits"]["retries"] == 0
    assert reply["request_id"] == _REQUEST
    assert reply["request_revision"] == _REVISION


def test_an_unknown_tool_is_refused() -> None:
    plans = _Plans()
    calls = {"n": 0}

    def complete(messages: list[dict], _tools: list[dict], _tool_choice: str = "auto") -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_launch",
                        "type": "function",
                        "function": {"name": "launch_gpu", "arguments": "{}"},
                    }
                ],
            }
        payload = json.loads(messages[-1]["content"])
        assert payload["status"] == "unknown_tool"
        raise RuntimeError("provider_unavailable")

    reply = AttemptCoordinator(_catalog(), complete, plans).ask("Launch it.", None, _REQUEST, _REVISION)
    assert reply["steps"][0]["status"] == "unknown_tool"
    assert reply["steps"][0]["tool"] == "launch_gpu"
    assert reply["conclusion_accepted"] is False
    assert reply["execution_connected"] is False
    assert plans.calls == []
    assert calls["n"] == 2


def test_an_invalid_reference_or_hash_blocks_the_synthesis() -> None:
    claimed = "ab" * 32

    def complete(messages: list[dict], _tools: list[dict], _tool_choice: str = "auto") -> dict:
        if not any(item.get("role") == "tool" for item in messages):
            return {"content": "", "tool_calls": [_EVIDENCE]}
        return {
            "content": f"See attempt.NOTREAL and hash {claimed}.",
            "tool_calls": [],
        }

    reply = _ask(complete)
    assert reply["status"] == "synthesis_rejected"
    assert reply["conclusion_accepted"] is False
    assert reply["text"].startswith("The coordinator output was rejected.")
    assert "attempt.NOTREAL" not in reply["text"]
    assert claimed not in reply["text"]
    assert "attempt.NOTREAL" in reply["rejected_output"]
    assert {item["code"] for item in reply["validation"]["findings"]} >= {"unknown_citation", "unknown_hash"}
    assert "secret" not in json.dumps(reply["validation"])


def test_an_inconsistent_synthesis_is_rejected_and_not_remembered() -> None:
    seen: list[list[dict]] = []

    def complete(messages: list[dict], _tools: list[dict], tool_choice: str = "auto") -> dict:
        seen.append(messages)
        if len(seen) == 1:
            return {"content": "", "tool_calls": [_EVIDENCE]}
        if len(seen) == 2:
            return {"content": "The recorded ranking_score is 0.91. The plan was approved.", "tool_calls": []}
        assert tool_choice == "auto"
        context = json.loads(str(messages[1]["content"]).removeprefix("Conversation context: "))
        assert "0.91" not in json.dumps(context["previous_conclusions"])
        assert "was approved" not in json.dumps(context["previous_conclusions"])
        return {"content": _ANSWER, "tool_calls": []}

    coordinator = AttemptCoordinator(_catalog(), complete)
    first = coordinator.ask("What is the score?", None, _REQUEST, _REVISION)
    assert first["status"] == "synthesis_rejected"
    assert first["conclusion_accepted"] is False
    assert "0.91" not in first["text"]
    assert "was approved" not in first["text"]
    assert "0.91" in first["rejected_output"]
    assert {item["code"] for item in first["validation"]["findings"]} >= {"divergent_score", "executed_decision"}
    second = coordinator.ask("Use only the recorded score.", first["conversation_id"], _REQUEST, _REVISION)
    assert second["status"] == "answered"
    assert second["conclusion_accepted"] is True


def test_a_token_budget_stops_before_another_tool_or_a_conclusion() -> None:
    calls = {"n": 0}

    def complete(_messages: list[dict], _tools: list[dict], _tool_choice: str = "auto") -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            return {"content": "", "tool_calls": [_EVIDENCE], "usage": {"total_tokens": 3, "input_tokens": 2, "output_tokens": 1}}
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": "call_launch",
                    "type": "function",
                    "function": {"name": "launch_gpu", "arguments": "{}"},
                }
            ],
            "usage": {"total_tokens": 3, "input_tokens": 1, "output_tokens": 2},
        }

    reply = AttemptCoordinator(
        _catalog(),
        complete,
        limits=CoordinatorLimits(token_budget=5),
    ).ask("Continue.", None, _REQUEST, _REVISION)
    assert calls["n"] == 2
    assert reply["status"] == "budget_interrupted"
    assert reply["conclusion_accepted"] is False
    assert reply["termination"]["stop"] == "token_budget"
    assert reply["usage"]["total_tokens"] == 6
    assert all(step["tool"] != "launch_gpu" for step in reply["steps"])
    assert "No conclusion was accepted." in reply["text"]


def test_omitted_provider_usage_does_not_spend_the_local_budget() -> None:
    def complete(messages: list[dict], _tools: list[dict], _tool_choice: str = "auto") -> dict:
        if not any(item.get("role") == "tool" for item in messages):
            return {"content": "", "tool_calls": [_EVIDENCE]}
        return {"content": _ANSWER, "tool_calls": []}

    reply = AttemptCoordinator(_catalog(), complete, limits=CoordinatorLimits(token_budget=1)).ask(
        "Why was model 2 selected?", None, _REQUEST, _REVISION
    )
    assert reply["status"] == "answered"
    assert reply["usage"]["total_tokens"] is None
    assert reply["usage"]["reported_calls"] == 0


def test_a_provider_failure_is_not_retried() -> None:
    calls = {"n": 0}

    def complete(_messages: list[dict], _tools: list[dict], _tool_choice: str = "auto") -> dict:
        calls["n"] += 1
        raise RuntimeError("provider_unavailable")

    reply = AttemptCoordinator(_catalog(), complete).ask("Why was model 2 selected?", None, _REQUEST, _REVISION)
    assert calls["n"] == 1
    assert reply["status"] == "provider_unavailable"
    assert reply["limits"]["retries"] == 0
    assert reply["conclusion_accepted"] is False
    assert reply["model_calls"] == 1


def test_the_model_call_limit_stops_the_loop() -> None:
    calls = {"n": 0}

    def complete(_messages: list[dict], _tools: list[dict], _tool_choice: str = "auto") -> dict:
        calls["n"] += 1
        return {"content": "", "tool_calls": [_EVIDENCE]}

    reply = AttemptCoordinator(_catalog(), complete, limits=CoordinatorLimits(max_model_calls=1)).ask(
        "Why was model 2 selected?", None, _REQUEST, _REVISION
    )
    assert calls["n"] == 1
    assert reply["status"] == "tool_failure"
    assert reply["termination"]["stop"] == "model_call_limit"
    assert reply["conclusion_accepted"] is False


def test_drafting_a_plan_does_not_approve_authorize_or_launch() -> None:
    plans = _Plans()

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
        return {"content": "The saved plan is unapproved. Generation has not started.", "tool_calls": []}

    reply = AttemptCoordinator(_catalog(), complete, plans).ask("Generate another binder.", None, _REQUEST, _REVISION)
    assert reply["status"] == "answered"
    assert reply["execution_connected"] is False
    assert plans.calls == ["draft"]


def test_sqlite_keeps_the_request_binding_and_drops_private_fields(tmp_path: Path) -> None:
    store = SQLiteCoordinatorTraceStore(tmp_path / "coordinator-traces.sqlite")

    def complete(messages: list[dict], _tools: list[dict], _tool_choice: str = "auto") -> dict:
        if not any(item.get("role") == "tool" for item in messages):
            return {"content": "", "tool_calls": [_EVIDENCE], "reasoning": "private chain"}
        return {"content": _ANSWER, "tool_calls": [], "api_key": "secret-key"}

    coordinator = AttemptCoordinator(
        _catalog(),
        complete,
        traces=store,
        model_record={"provider": "openrouter", "name": "test-model", "api_key": "secret-key"},
    )
    coordinator.ask("Why was model 2 selected?", None, _REQUEST, _REVISION)
    coordinator.ask("Other request.", None, "request_other1", 1)
    status, payload = handle_attempt_coordinator_read(
        coordinator,
        f"/api/attempt-coordinator?request_id={_REQUEST}&request_revision={_REVISION}",
    )
    assert status == 200
    assert payload["traces"][0]["request_id"] == _REQUEST
    refused, empty = handle_attempt_coordinator_read(coordinator, "/api/attempt-coordinator")
    assert refused == 400
    assert empty["traces"] == []
    store.close()
    reopened = SQLiteCoordinatorTraceStore(tmp_path / "coordinator-traces.sqlite")
    saved = reopened.for_request(_REQUEST, _REVISION)
    assert len(saved) == 1
    assert saved[0]["request_revision"] == _REVISION
    assert saved[0]["model"] == {"provider": "openrouter", "name": "test-model"}
    assert saved[0]["conclusion_accepted"] is True
    assert saved[0]["agent_executed"] is False
    rendered = json.dumps(saved)
    assert "secret-key" not in rendered
    assert _hidden_keys(saved) == set()
    assert reopened.for_request("request_other1", 1)[0]["source_query"] == "Other request."
    assert reopened.for_request(_REQUEST, 1) == []
    assert payload["traces"][0]["trace_id"] == saved[0]["trace_id"]
    reopened.close()


def test_the_provider_body_keeps_the_per_call_limit_and_drops_reasoning() -> None:
    seen: dict[str, object] = {}

    def transport(*, url: str, headers: dict[str, str], body: bytes, timeout: float = 30) -> tuple[int, bytes]:
        seen["timeout"] = timeout
        seen["body"] = json.loads(body)
        seen["authorization"] = headers["authorization"]
        return 200, json.dumps(
            {
                "choices": [{"message": {"content": "Done.", "tool_calls": [], "reasoning": "private"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3, "account_id": "hidden"},
            }
        ).encode()

    model = ChatCompletionModel(
        provider="openrouter",
        model="test-model",
        api_key="secret-key",
        url="https://openrouter.ai/api/v1/chat/completions",
        transport=transport,
    )
    message = _complete_with(model, CoordinatorLimits(max_output_tokens_per_call=40, call_timeout_seconds=9))(
        [{"role": "user", "content": "hello"}],
        [{"type": "function"}],
        "required",
    )
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["max_tokens"] == 40
    assert seen["timeout"] == 9
    assert "secret-key" not in json.dumps(body)
    assert message == {
        "content": "Done.",
        "tool_calls": [],
        "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
    }


def test_limits_reject_an_invalid_budget_and_document_their_scope() -> None:
    limits = resolve_coordinator_limits({})
    assert limits.max_model_calls == 6
    assert limits.max_output_tokens_per_call == 1500
    assert limits.token_budget is None
    assert limits.retries == 0
    assert any("not a total cost ceiling" in item for item in VALIDATION_LIMITATIONS)
    with pytest.raises(CoordinatorLimitsError):
        resolve_coordinator_limits({"CELLERTZ_COORDINATOR_TOKEN_BUDGET": "many"})


class _Plans:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def draft(self, _conversation_id: str, _proposal: dict) -> dict:
        self.calls.append("draft")
        return {
            "plan_id": "plan_test",
            "revision": 1,
            "approved": False,
            "execution_connected": False,
            "questions": [],
            "status": "ok",
        }

    def approve(self, *_args: object, **_kwargs: object) -> None:
        self.calls.append("approve")
        raise AssertionError("approve")

    def authorize(self, *_args: object, **_kwargs: object) -> None:
        self.calls.append("authorize")
        raise AssertionError("authorize")

    def launch(self, *_args: object, **_kwargs: object) -> None:
        self.calls.append("launch")
        raise AssertionError("launch")


def _hidden_keys(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"api_key", "authorization", "reasoning", "reasoning_content", "chain_of_thought"}:
                found.add(key)
            found.update(_hidden_keys(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_hidden_keys(item))
    return found


def _ask(complete) -> dict:
    return AttemptCoordinator(_catalog(), complete).ask("Check the evidence.", None, _REQUEST, _REVISION)


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
