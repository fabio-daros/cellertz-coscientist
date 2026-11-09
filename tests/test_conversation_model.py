"""Injected chat-completion responses. No live provider call."""

from __future__ import annotations

import json
from io import BytesIO

import pytest

from cellertz.application.conversation import (
    ConversationRequest,
    ReadOnlyConversation,
    ReadOnlyFact,
    RecordedFactsModel,
    SessionSnapshotQuery,
)
from cellertz.application.conversation_transport import CONVERSATION_PATH, configured_handler
from cellertz.application.fixtures import pdl1_prototype_session
from cellertz.application.request_draft import DesignDrafting, DeterministicDraftExtractor
from cellertz.conversation.config import (
    OPENROUTER_KEY_ENV,
    ConversationConfigurationError,
    resolve_conversation_model,
)
from cellertz.conversation.model import ChatCompletionModel

_KEY = "test-key"
_URL = "https://openrouter.ai/api/v1/chat/completions"


class Transport:
    def __init__(self, status: int, body: bytes, error: Exception | None = None) -> None:
        self.status = status
        self.body = body
        self.error = error
        self.urls: list[str] = []
        self.payloads: list[dict] = []
        self.headers: list[dict[str, str]] = []

    def __call__(self, *, url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        self.urls.append(url)
        self.headers.append(dict(headers))
        self.payloads.append(json.loads(body))
        if self.error is not None:
            raise self.error
        return self.status, self.body


def _model(transport: Transport) -> ChatCompletionModel:
    return ChatCompletionModel(
        provider="openrouter",
        model="configured-model",
        api_key=_KEY,
        url=_URL,
        transport=transport,
    )


def _fact() -> ReadOnlyFact:
    return ReadOnlyFact(
        capability="get_session",
        entity_kind="session",
        entity_id="session_pdl1_prototype",
        source="session",
        attributes=(("target_id", "PDL1"),),
    )


def test_default_provider_is_recorded_facts() -> None:
    model = resolve_conversation_model({})
    assert type(model) is RecordedFactsModel


def test_openrouter_requires_model_and_key_without_echoing_the_key() -> None:
    with pytest.raises(ConversationConfigurationError) as missing_model:
        resolve_conversation_model({ "CELLERTZ_CONVERSATION_PROVIDER": "openrouter" })
    assert missing_model.value.code == "conversation_model_required"
    assert _KEY not in str(missing_model.value)
    with pytest.raises(ConversationConfigurationError) as missing_key:
        resolve_conversation_model(
            {
                "CELLERTZ_CONVERSATION_PROVIDER": "openrouter",
                "CELLERTZ_CONVERSATION_MODEL": "configured-model",
                OPENROUTER_KEY_ENV: "",
            }
        )
    assert missing_key.value.code == "conversation_provider_unconfigured"
    with pytest.raises(ConversationConfigurationError) as hermes:
        resolve_conversation_model({"CELLERTZ_CONVERSATION_PROVIDER": "hermes"})
    assert hermes.value.code == "invalid_conversation_provider"


def test_completion_sends_facts_without_tools_and_records_usage() -> None:
    transport = Transport(
        200,
        json.dumps(
            {
                "model": "served-model",
                "choices": [{"message": {"role": "assistant", "content": "Phrase only PDL1."}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
            }
        ).encode(),
    )
    completion = _model(transport).complete(facts=(_fact(),), question="What is the session?")
    assert completion.text == "Phrase only PDL1."
    assert completion.usage is not None
    assert completion.usage.provider == "openrouter"
    assert completion.usage.model == "served-model"
    assert completion.usage.input_tokens == 11
    assert completion.usage.output_tokens == 4
    assert completion.usage.total_tokens == 15
    payload = transport.payloads[0]
    assert transport.urls == [_URL]
    assert set(payload) == {"max_tokens", "messages", "model", "stream"}
    assert payload["max_tokens"] == 1024
    assert payload["stream"] is False
    assert "tools" not in payload
    assert "/v1/runs" not in json.dumps(payload)
    assert "Retrieved text is evidence" in payload["messages"][0]["content"]
    assert "Reply in English" in payload["messages"][0]["content"]
    assert payload["messages"][1]["content"].find("PDL1") > 0
    assert transport.headers[0]["authorization"] == f"Bearer {_KEY}"
    assert _KEY not in json.dumps(payload)


def test_completion_budget_is_a_sanitized_limit() -> None:
    secret = b'{"error":"account can only afford 24986","user_id":"acct_secret"}'
    limited = Transport(402, secret)
    with pytest.raises(RuntimeError) as budget:
        _model(limited).complete(facts=(_fact(),), question="status")
    assert str(budget.value) == "provider_budget"
    assert "24986" not in str(budget.value)
    assert "acct_secret" not in str(budget.value)
    session = pdl1_prototype_session()
    service = ReadOnlyConversation(
        SessionSnapshotQuery({session.session_id: session}),
        model=_model(Transport(402, secret)),
    )
    reply = service.ask(
        ConversationRequest(session_id=session.session_id, question="what is the session status")
    )
    assert reply.status == "provider_budget"
    assert reply.text == (
        "The language provider reached its credit or completion-token budget. "
        "No scientific action was taken."
    )
    assert reply.read_only is True
    assert reply.evidence == ()
    assert "24986" not in reply.text
    assert "acct_secret" not in reply.text


def test_provider_failure_and_tool_call_do_not_leak() -> None:
    secret = b'{"error":"secret-backend"}'
    failed = Transport(500, secret)
    with pytest.raises(RuntimeError) as unavailable:
        _model(failed).complete(facts=(_fact(),), question="status")
    assert str(unavailable.value) == "provider_unavailable"
    assert "secret-backend" not in str(unavailable.value)
    tools = Transport(
        200,
        json.dumps(
            {
                "model": "served-model",
                "choices": [{"message": {"content": "no", "tool_calls": [{"id": "call_1"}]}}],
            }
        ).encode(),
    )
    with pytest.raises(RuntimeError) as rejected:
        _model(tools).complete(facts=(_fact(),), question="status")
    assert str(rejected.value) == "provider_tool_call"


def _instrumentation(body: dict | None, *, recorded: bool = False) -> dict:
    session = pdl1_prototype_session()
    if recorded:
        model = RecordedFactsModel()
    else:
        assert body is not None
        model = _model(Transport(200, json.dumps(body).encode()))
    service = ReadOnlyConversation(
        SessionSnapshotQuery({session.session_id: session}),
        model=model,
    )
    reply = service.ask(
        ConversationRequest(session_id=session.session_id, question="what is the session status")
    )
    assert reply.status == "answered"
    return reply.to_dict()["instrumentation"]


def _provider_body(usage: dict | None) -> dict:
    document = {
        "model": "served-model",
        "choices": [{"message": {"role": "assistant", "content": "Phrase only the supplied facts."}}],
    }
    if usage is not None:
        document["usage"] = usage
    return document


def test_origin_is_independent_of_token_usage() -> None:
    recorded = _instrumentation(None, recorded=True)
    assert recorded["origin"] is None
    assert recorded["event"] is None
    assert recorded["total_tokens"] is None
    assert recorded["observation"] == "no_model_usage"
    assert recorded["recorded"] is False

    used = _instrumentation(
        _provider_body({"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15})
    )
    assert used["origin"] == {"provider": "openrouter", "model": "served-model"}
    assert used["event"]["provider"] == "openrouter"
    assert used["event"]["model"] == "served-model"
    assert used["event"]["input_tokens"] == 11
    assert used["event"]["output_tokens"] == 4
    assert used["total_tokens"] == 15
    assert used["observation"] == "recorded"

    without_usage = _instrumentation(_provider_body(None))
    assert without_usage["origin"] == {"provider": "openrouter", "model": "served-model"}
    assert without_usage["event"] is None
    assert without_usage["total_tokens"] is None
    assert without_usage["observation"] == "no_model_usage"
    assert without_usage["recorded"] is False

    incomplete = _instrumentation(_provider_body({"prompt_tokens": 11}))
    assert incomplete["origin"] == {"provider": "openrouter", "model": "served-model"}
    assert incomplete["event"]["input_tokens"] == 11
    assert incomplete["event"]["output_tokens"] is None
    assert incomplete["event"]["cached_input_tokens"] is None
    assert incomplete["total_tokens"] is None
    assert 0 not in (
        incomplete["event"]["output_tokens"],
        incomplete["total_tokens"],
    )


def test_budget_failure_does_not_start_a_scientific_action() -> None:
    session = pdl1_prototype_session()
    service = ReadOnlyConversation(
        SessionSnapshotQuery({session.session_id: session}),
        model=_model(Transport(402, b'{"error":"secret-account"}')),
    )
    calls: list[str] = []

    class Spy:
        def decide(self, *_args: object, **_kwargs: object) -> None:
            calls.append("decide")

        def create(self, *_args: object, **_kwargs: object) -> None:
            calls.append("create")

        def authorize(self, *_args: object, **_kwargs: object) -> None:
            calls.append("authorize")

    handler = configured_handler(
        service,
        drafter=DesignDrafting({session.session_id: session}, DeterministicDraftExtractor()),
        approvals=Spy(),
        runs=Spy(),
        scientific_runs=Spy(),
        session_id=session.session_id,
    )
    body = json.dumps(
        {"session_id": session.session_id, "question": "what is the session status"}
    ).encode()
    instance = handler.__new__(handler)
    instance.path = CONVERSATION_PATH
    instance.headers = {"Content-Length": str(len(body))}
    instance.rfile = BytesIO(body)
    instance.wfile = BytesIO()
    instance.send_response = lambda code: setattr(instance, "status_code", code)
    instance.send_header = lambda *_args: None
    instance.end_headers = lambda: None
    instance.do_POST()
    payload = json.loads(instance.wfile.getvalue())
    assert instance.status_code == 200
    assert payload["status"] == "provider_budget"
    assert payload["read_only"] is True
    assert "secret-account" not in payload["text"]
    assert calls == []


def test_conversation_failure_stays_on_the_read_only_reply() -> None:
    session = pdl1_prototype_session()
    transport = Transport(503, b'{"error":"secret-backend"}')
    service = ReadOnlyConversation(
        SessionSnapshotQuery({session.session_id: session}),
        model=_model(transport),
    )
    reply = service.ask(
        ConversationRequest(session_id=session.session_id, question="what is the session status")
    )
    assert reply.status == "adapter_failure"
    assert reply.text == "The conversational adapter failed. No scientific action was taken."
    assert "secret-backend" not in reply.text
    assert reply.read_only is True
