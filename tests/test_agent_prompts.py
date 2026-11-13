"""The prompt seam, and what a stored body may not do.

The Supervisor prompt was one constant before it was split into a task body
and its governance clauses. The split must not have changed a character, so
the text is pinned by hash: if someone edits either half, this fails and the
comparison between analyses written before and after is not lost silently.
"""

from __future__ import annotations

import pytest

from cellertz.application.agent_prompts import (
    BUILTIN,
    resolve_prompt,
    set_agent_prompt_provider,
)
from cellertz.application.request_supervisor import (
    _GOVERNANCE,
    _QUESTION,
    AGENT_KEY,
    resolved_prompt,
)

# The Supervisor prompt as it stood before it was ever split.
RECORDED_SHA = "495fdd31a26980f8e328c1012b2e78c134315ca26050a3d4a3b969a3b68afd9f"


class StoredBody:
    def __init__(self, version: int, body: str) -> None:
        self.version = version
        self.body = body
        self.asked: list[str] = []

    def task_body(self, agent_key: str):
        self.asked.append(agent_key)
        return (self.version, self.body)


class BrokenStore:
    def task_body(self, agent_key: str):
        raise RuntimeError("the prompt store is down")


@pytest.fixture(autouse=True)
def _clear_provider():
    yield
    set_agent_prompt_provider(None)


def test_the_split_did_not_change_the_supervisor_prompt():
    assert resolved_prompt().sha256 == RECORDED_SHA
    assert resolved_prompt().text == _QUESTION


def test_without_a_provider_the_builtin_body_is_used():
    resolved = resolved_prompt()
    assert resolved.origin == BUILTIN
    assert resolved.version is None


def test_a_stored_body_replaces_only_the_task_half():
    store = StoredBody(7, "You are a test Supervisor. ")
    set_agent_prompt_provider(store)
    resolved = resolved_prompt()
    assert store.asked == [AGENT_KEY]
    assert resolved.origin == "stored"
    assert resolved.version == 7
    assert resolved.text.startswith("You are a test Supervisor. ")
    # Every governance clause survives a stored body.
    assert resolved.text.endswith(_GOVERNANCE)
    assert "Do not plan, approve, authorize, or start a run." in resolved.text


def test_an_empty_stored_body_does_not_strip_the_prompt_to_governance():
    set_agent_prompt_provider(StoredBody(2, "   "))
    assert resolved_prompt().sha256 == RECORDED_SHA


def test_a_store_that_fails_does_not_stop_the_agent():
    set_agent_prompt_provider(BrokenStore())
    assert resolved_prompt().sha256 == RECORDED_SHA


def test_provenance_names_the_version_that_produced_the_text():
    set_agent_prompt_provider(StoredBody(3, "A tuned body. "))
    provenance = resolved_prompt().provenance()
    assert provenance["agent_key"] == AGENT_KEY
    assert provenance["origin"] == "stored"
    assert provenance["version"] == 3
    assert provenance["sha256"] != RECORDED_SHA


def test_two_bodies_give_two_different_hashes():
    first = resolve_prompt("a", "one. ", _GOVERNANCE).sha256
    second = resolve_prompt("a", "two. ", _GOVERNANCE).sha256
    assert first != second


def test_the_supervisor_is_in_the_catalog_with_its_builtin_body():
    from cellertz.application.agent_prompts import builtin_body, known_agents

    keys = [key for key, _body in known_agents()]
    assert AGENT_KEY in keys
    assert builtin_body(AGENT_KEY) is not None
    assert resolve_prompt(AGENT_KEY, builtin_body(AGENT_KEY), _GOVERNANCE).sha256 == RECORDED_SHA


def test_an_unknown_agent_has_no_builtin_body():
    from cellertz.application.agent_prompts import builtin_body

    assert builtin_body("not_an_agent") is None


def test_a_body_saved_through_a_form_still_joins_with_a_space():
    # A form strips trailing whitespace. Before the separator was explicit,
    # this produced "saved text.The facts keep three sources separate."
    from cellertz.application.request_supervisor import _TASK_BODY

    set_agent_prompt_provider(StoredBody(1, _TASK_BODY.rstrip()))
    text = resolved_prompt().text
    assert "saved text. The facts" in text
    assert "saved text.The facts" not in text
    # Stripping is all that differed, so the sent text is the built-in one.
    assert resolved_prompt().sha256 == RECORDED_SHA


def test_a_body_with_extra_blank_space_does_not_double_the_separator():
    set_agent_prompt_provider(StoredBody(1, "A tuned body.   "))
    assert "A tuned body. The facts" in resolved_prompt().text
