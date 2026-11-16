"""Which model an agent reasons with.

The provider, its key and its base stay the installation's. Only the model
is per agent, so a review of binding-region evidence can use a larger model
than a delegation that writes two JSON fields.
"""

from __future__ import annotations

import pytest

from cellertz.application.agent_runtime import (
    model_for_agent,
    resolve_agent_model_id,
    set_agent_limits_provider,
)

LIVE = {
    "CELLERTZ_CONVERSATION_PROVIDER": "openrouter",
    "CELLERTZ_CONVERSATION_MODEL": "openai/gpt-4.1-mini",
    "OPENROUTER_API_KEY": "a-key",
}


class Store:
    def __init__(self, **fields) -> None:
        self.fields = fields

    def limits(self, agent_key: str):
        return dict(self.fields)


class Fallback:
    """Stands in for the model the service was given."""


@pytest.fixture(autouse=True)
def _clear_provider():
    yield
    set_agent_limits_provider(None)


def test_without_a_stored_model_the_services_own_is_used():
    assert model_for_agent("a.task", Fallback) is Fallback


def test_a_blank_model_is_not_a_choice():
    set_agent_limits_provider(Store(model="   "))
    assert resolve_agent_model_id("a.task") is None
    assert model_for_agent("a.task", Fallback) is Fallback


def test_a_malformed_id_is_refused_rather_than_sent():
    set_agent_limits_provider(Store(model="claude-opus-5-5"))
    assert resolve_agent_model_id("a.task") is None


def test_a_stored_id_is_read():
    set_agent_limits_provider(Store(model="anthropic/claude-opus-5-5"))
    assert resolve_agent_model_id("a.task") == "anthropic/claude-opus-5-5"


def test_the_agents_model_is_built_at_the_installations_provider(monkeypatch):
    for name, value in LIVE.items():
        monkeypatch.setenv(name, value)
    set_agent_limits_provider(Store(model="anthropic/claude-opus-5-5"))
    built = model_for_agent("a.task", Fallback)
    assert built is not Fallback
    assert built._model == "anthropic/claude-opus-5-5"
    # The provider and its key are the installation's, not the row's.
    assert built._provider == "openrouter"


def test_two_agents_may_reason_with_different_models(monkeypatch):
    for name, value in LIVE.items():
        monkeypatch.setenv(name, value)

    class PerAgent:
        def limits(self, agent_key: str):
            return {"model": "anthropic/claude-opus-5-5"} if agent_key == "big.task" else {}

    set_agent_limits_provider(PerAgent())
    assert model_for_agent("big.task", Fallback)._model == "anthropic/claude-opus-5-5"
    assert model_for_agent("small.task", Fallback) is Fallback


def test_without_a_live_provider_the_override_is_ignored(monkeypatch):
    monkeypatch.delenv("CELLERTZ_CONVERSATION_PROVIDER", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    set_agent_limits_provider(Store(model="anthropic/claude-opus-5-5"))
    # A recorded installation has no provider to override.
    built = model_for_agent("a.task", Fallback)
    assert built.__class__.__name__ in {"Fallback", "RecordedFactsModel"}
