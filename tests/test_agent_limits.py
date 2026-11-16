"""Per-agent call limits, and what a stored row may not do.

The ceiling that was raised from 400 to 1200 by a published script is the
case these tests are written around: a stored row must be able to do the same
thing, without being able to spend without bound.
"""

from __future__ import annotations

import pytest

from cellertz.application.agent_runtime import (
    BUILTIN,
    MAX_OUTPUT_TOKENS_CEILING,
    STORED,
    AgentLimits,
    default_limits,
    resolve_limits,
    set_agent_limits_provider,
)
from cellertz.application.plan_configuration_suggestion import SPECIALIST_KEY, limits_for_suggestion
from cellertz.application.request_supervisor import AGENT_KEY, resolve_supervisor_limits

DEFAULTS = AgentLimits(
    max_output_tokens=400, max_model_calls=8, token_budget=2000, call_timeout_seconds=30.0
)


class Store:
    def __init__(self, **fields) -> None:
        self.fields = fields

    def limits(self, agent_key: str):
        return dict(self.fields)


class BrokenStore:
    def limits(self, agent_key: str):
        raise RuntimeError("the limits store is down")


@pytest.fixture(autouse=True)
def _clear_provider():
    yield
    set_agent_limits_provider(None)


def test_without_a_store_the_defaults_stand():
    resolved = resolve_limits("any.agent", DEFAULTS)
    assert resolved.origin == BUILTIN
    assert resolved.limits == DEFAULTS


def test_one_stored_field_leaves_the_others_alone():
    set_agent_limits_provider(Store(max_output_tokens=1200))
    limits = resolve_limits("any.agent", DEFAULTS).limits
    assert limits.max_output_tokens == 1200
    assert limits.max_model_calls == DEFAULTS.max_model_calls
    assert limits.token_budget == DEFAULTS.token_budget


def test_a_blank_field_keeps_the_default():
    set_agent_limits_provider(Store(max_output_tokens=None, token_budget=None))
    assert resolve_limits("any.agent", DEFAULTS).limits == DEFAULTS


def test_a_stored_value_cannot_exceed_the_ceiling():
    set_agent_limits_provider(Store(max_output_tokens=10_000_000))
    limits = resolve_limits("any.agent", DEFAULTS).limits
    assert limits.max_output_tokens == MAX_OUTPUT_TOKENS_CEILING


def test_a_nonsense_value_is_ignored_rather_than_applied():
    set_agent_limits_provider(Store(max_model_calls=0, call_timeout_seconds=-5))
    assert resolve_limits("any.agent", DEFAULTS).limits == DEFAULTS


def test_a_store_that_fails_does_not_stop_the_agent():
    set_agent_limits_provider(BrokenStore())
    assert resolve_limits("any.agent", DEFAULTS).limits == DEFAULTS


def test_the_origin_says_whether_an_installation_changed_anything():
    set_agent_limits_provider(Store(max_output_tokens=DEFAULTS.max_output_tokens))
    assert resolve_limits("any.agent", DEFAULTS).origin == BUILTIN
    set_agent_limits_provider(Store(max_output_tokens=1200))
    assert resolve_limits("any.agent", DEFAULTS).origin == STORED


def test_the_supervisor_reads_its_stored_ceiling():
    assert resolve_supervisor_limits({}).max_output_tokens == 400
    set_agent_limits_provider(Store(max_output_tokens=900))
    assert resolve_supervisor_limits({}).max_output_tokens == 900


def test_the_specialist_ceiling_can_be_raised_without_a_deploy():
    shared = resolve_supervisor_limits({})
    assert limits_for_suggestion(shared, {}).max_output_tokens == 1200
    set_agent_limits_provider(Store(max_output_tokens=2400))
    assert limits_for_suggestion(shared, {}).max_output_tokens == 2400


def test_every_registered_agent_declares_its_defaults():
    for key in (AGENT_KEY, SPECIALIST_KEY):
        assert default_limits(key) is not None
