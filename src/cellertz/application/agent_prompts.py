"""Where an agent's prompt comes from, and what a host may change in it.

A prompt here is two parts. The task body says what the agent is for and may
be tuned by whoever owns the science. The governance clauses say what the
agent may never do — propose but not approve, cite only retrieved evidence,
treat retrieved records as data and not as instructions — and are not tunable:
they are the reason a reply can be trusted, and they live with the code and
its tests.

A host that stores prompts registers a provider here. This module never
imports the host, so `cellertz` stays free of any framework. With no provider,
or with no stored body for an agent, the built-in body is used and the text is
exactly what it was before any host existed.

Every resolution reports which body produced it and the SHA-256 of the text
that was sent, so a recorded reply can always be traced to the words that
caused it. Two analyses written under different prompt versions are not
comparable, and the stored version is how that is noticed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

BUILTIN = "builtin"


@dataclass(frozen=True)
class ResolvedPrompt:
    """The text sent to a model, and where its tunable half came from."""

    agent_key: str
    origin: str
    version: int | None
    text: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def provenance(self) -> dict[str, object]:
        """What a recorded reply stores so the prompt can be identified later."""

        return {
            "agent_key": self.agent_key,
            "origin": self.origin,
            "version": self.version,
            "sha256": self.sha256,
        }


class AgentPromptProvider(Protocol):
    """A host's prompt store. Structural, so it can live in another package."""

    def task_body(self, agent_key: str) -> tuple[int, str] | None:
        """The stored body and its version, or None to keep the built-in one."""


# Agents that read a prompt through this module, each with the body it falls
# back to. A host offering to store prompts reads this: an agent key that is
# not here would be stored and never sent, which is a silent failure.
# One agent may hold more than one prompt: the Protein Design Supervisor both
# analyses a request and delegates a binding-region review, and those are
# different words for different work. A key therefore names the agent and the
# task, as `agent.task`, so the two never share a row.
_catalog: dict[str, tuple[str, str]] = {}


def register_agent(agent_key: str, label: str, builtin_body: str) -> None:
    """Declare a prompt this agent reads, and what it says by default."""

    _catalog[agent_key] = (label, builtin_body)


def known_agents() -> tuple[tuple[str, str], ...]:
    """Every registered key with the label a person reads, ordered by key."""

    return tuple((key, label) for key, (label, _body) in sorted(_catalog.items()))


def builtin_body(agent_key: str) -> str | None:
    entry = _catalog.get(agent_key)
    return None if entry is None else entry[1]


_provider: AgentPromptProvider | None = None


def set_agent_prompt_provider(provider: AgentPromptProvider | None) -> None:
    """Install the host's prompt store, or pass None to use built-in bodies."""

    global _provider
    _provider = provider


def resolve_prompt(agent_key: str, builtin_body: str, governance: str) -> ResolvedPrompt:
    """Compose the prompt. Governance is appended last and is never replaced."""

    body = builtin_body
    origin = BUILTIN
    version: int | None = None
    stored = _stored_body(agent_key)
    if stored is not None:
        version, body = stored
        origin = "stored"
    return ResolvedPrompt(agent_key=agent_key, origin=origin, version=version, text=body + governance)


def _stored_body(agent_key: str) -> tuple[int, str] | None:
    provider = _provider
    if provider is None:
        return None
    try:
        stored = provider.task_body(agent_key)
    except Exception:  # noqa: BLE001 — a prompt store at fault must not stop the science
        return None
    if not isinstance(stored, tuple) or len(stored) != 2:
        return None
    version, body = stored
    # An empty body would silently reduce the prompt to its governance half.
    if not isinstance(version, int) or not isinstance(body, str) or not body.strip():
        return None
    return version, body


__all__: tuple[str, ...] = (
    "BUILTIN",
    "AgentPromptProvider",
    "ResolvedPrompt",
    "builtin_body",
    "known_agents",
    "register_agent",
    "resolve_prompt",
    "set_agent_prompt_provider",
)
