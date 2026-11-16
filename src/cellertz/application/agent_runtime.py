"""Per-agent call limits, and where an installation may change them.

An agent's limits are not one setting. The Supervisor answers a request in a
few hundred tokens; the Hotspot Identification Agent returns a residue list
and needs three times that. The code has always known this — the ceilings
differ per agent — but it knew it as constants, so raising one took a code
change, a publish and a service restart. That happened: the specialist's
reply was truncated at 400 output tokens, which a hotspot list does not fit.

A host that stores limits registers a provider here. This module never
imports the host. With no provider, or no stored row, the built-in defaults
are used and the limits are exactly what they were.

Unlike a prompt, a limit is not versioned. What a run actually spent is
already recorded in its instrumentation, so the configuration states the
intent and the record states the fact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Protocol

BUILTIN = "builtin"
STORED = "stored"

# Ceilings an installation cannot exceed, matching what the environment route
# already allowed. A stored value above one of these is clamped rather than
# refused: the agent keeps working, at a limit the platform is willing to pay.
MAX_OUTPUT_TOKENS_CEILING = 4000
MAX_CALLS_CEILING = 20
TOKEN_BUDGET_CEILING = 20000
TIMEOUT_CEILING_SECONDS = 120.0


@dataclass(frozen=True)
class AgentLimits:
    """What one agent may spend on one call, and across a request."""

    max_output_tokens: int
    max_model_calls: int
    token_budget: int
    call_timeout_seconds: float

    def to_dict(self) -> dict[str, object]:
        return {
            "max_output_tokens": self.max_output_tokens,
            "max_model_calls": self.max_model_calls,
            "token_budget": self.token_budget,
            "call_timeout_seconds": self.call_timeout_seconds,
        }


@dataclass(frozen=True)
class ResolvedLimits:
    """The limits in force, and whether an installation set them."""

    agent_key: str
    origin: str
    limits: AgentLimits

    def provenance(self) -> dict[str, object]:
        return {"agent_key": self.agent_key, "origin": self.origin, **self.limits.to_dict()}


class AgentLimitsProvider(Protocol):
    """A host's store of limits. Structural, so it may live in another package."""

    def limits(self, agent_key: str) -> dict[str, float | int | None] | None:
        """Stored fields for this agent. A missing or None field keeps the default."""


_defaults: dict[str, AgentLimits] = {}
_provider: AgentLimitsProvider | None = None


def register_agent_limits(agent_key: str, defaults: AgentLimits) -> None:
    """Declare what this agent spends when nothing is stored."""

    _defaults[agent_key] = defaults


def default_limits(agent_key: str) -> AgentLimits | None:
    return _defaults.get(agent_key)


def set_agent_limits_provider(provider: AgentLimitsProvider | None) -> None:
    """Install the host's store, or pass None to use built-in defaults."""

    global _provider
    _provider = provider


def resolve_limits(agent_key: str, defaults: AgentLimits) -> ResolvedLimits:
    """Apply any stored field over the defaults, within the ceilings."""

    stored = _stored(agent_key)
    if not stored:
        return ResolvedLimits(agent_key=agent_key, origin=BUILTIN, limits=defaults)
    limits = replace(
        defaults,
        max_output_tokens=_bounded(
            stored.get("max_output_tokens"), defaults.max_output_tokens, MAX_OUTPUT_TOKENS_CEILING
        ),
        max_model_calls=_bounded(stored.get("max_model_calls"), defaults.max_model_calls, MAX_CALLS_CEILING),
        token_budget=_bounded(stored.get("token_budget"), defaults.token_budget, TOKEN_BUDGET_CEILING),
        call_timeout_seconds=_bounded_seconds(
            stored.get("call_timeout_seconds"), defaults.call_timeout_seconds
        ),
    )
    changed = limits != defaults
    return ResolvedLimits(agent_key=agent_key, origin=STORED if changed else BUILTIN, limits=limits)


# An OpenRouter model id: a vendor, a slash, and the model's name. Validated
# here as well as in the host, because a store is not the only way in.
MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._:-]*$")


def resolve_agent_model_id(agent_key: str) -> str | None:
    """The model this agent reasons with, or None to use the installation's."""

    stored = _stored(agent_key).get("model")
    if not isinstance(stored, str):
        return None
    candidate = stored.strip()
    return candidate if MODEL_ID.match(candidate) else None


def model_for_agent(agent_key: str, fallback: object) -> object:
    """The agent's own model, or the one the service was given.

    The provider, its key and its base stay the installation's. Only which
    model answers is per agent, because a delegation needs less of a model
    than a review of binding-region evidence does.
    """

    model_id = resolve_agent_model_id(agent_key)
    if model_id is None:
        return fallback
    from cellertz.conversation.config import (
        ConversationConfigurationError,
        resolve_conversation_model,
    )

    try:
        return resolve_conversation_model(model_override=model_id)
    except ConversationConfigurationError:
        # The installation has no live provider, so there is nothing to
        # override and the recorded model it already holds is the answer.
        return fallback


def _stored(agent_key: str) -> dict[str, float | int | None]:
    provider = _provider
    if provider is None:
        return {}
    try:
        stored = provider.limits(agent_key)
    except Exception:  # noqa: BLE001 — a store at fault must not stop the science
        return {}
    return stored if isinstance(stored, dict) else {}


def _bounded(value: object, fallback: int, ceiling: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        return fallback
    return min(value, ceiling)


def _bounded_seconds(value: object, fallback: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return fallback
    return min(float(value), TIMEOUT_CEILING_SECONDS)


__all__: tuple[str, ...] = (
    "BUILTIN",
    "STORED",
    "AgentLimits",
    "AgentLimitsProvider",
    "ResolvedLimits",
    "default_limits",
    "model_for_agent",
    "register_agent_limits",
    "resolve_agent_model_id",
    "resolve_limits",
    "set_agent_limits_provider",
)
