"""Stored task bodies for the agents' prompts.

A row is the tunable half of one agent's prompt. The governance clauses are
not here: they live with the code and its tests, and are appended to whatever
this table holds. A body stored here can change what an agent looks for. It
cannot remove what the agent may never do.

Rows are versions, not drafts. A new version is a new row, because an
analysis already recorded names the version that produced it, and editing
that text in place would quietly make past and future replies look
comparable when they are not.
"""

from __future__ import annotations

from django.core.validators import RegexValidator
from django.db import models

from cellertz.application.agent_prompts import agent_label

# `agent.task`: one agent may hold several prompts, and each is its own key.
AGENT_KEY = RegexValidator(
    r"^[a-z0-9_]+(\.[a-z0-9_]+)*$",
    "A key is lowercase words joined by underscores, with tasks after a dot.",
)


class AgentPrompt(models.Model):
    agent_key = models.CharField(
        max_length=96,
        validators=[AGENT_KEY],
        help_text="The agent and task this body belongs to, as the code names it.",
    )
    version = models.PositiveIntegerField(
        help_text="Higher wins. The highest version is the one that is sent.",
    )
    body = models.TextField(
        help_text="The task half of the prompt. Governance clauses are added after it.",
    )
    note = models.TextField(
        blank=True,
        help_text="Why this version exists. Read by whoever compares two analyses.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["agent_key", "version"], name="unique_agent_prompt_version"),
        ]
        ordering = ("agent_key", "-version")

    @property
    def label(self) -> str:
        """The agent's readable name. The key is an identifier, not a name."""

        return agent_label(self.agent_key)

    def __str__(self) -> str:
        return f"{self.label} v{self.version}"


class AgentLimitsConfig(models.Model):
    """What one agent may spend. A blank field keeps the code's default.

    Unlike a prompt, this is not versioned. What a call actually spent is
    already recorded in the run's instrumentation, so this row states the
    intent and the record states the fact.
    """

    agent_key = models.CharField(
        max_length=96,
        unique=True,
        validators=[AGENT_KEY],
        help_text="The agent and task these limits apply to.",
    )
    max_output_tokens = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Tokens one reply may use. Raise it when a reply is being truncated.",
    )
    max_model_calls = models.PositiveIntegerField(
        null=True, blank=True, help_text="Model calls allowed while answering one request."
    )
    token_budget = models.PositiveIntegerField(
        null=True, blank=True, help_text="Tokens allowed across those calls."
    )
    call_timeout_seconds = models.FloatField(
        null=True, blank=True, help_text="Seconds one call may take before it is abandoned."
    )
    note = models.TextField(blank=True, help_text="Why these limits differ from the default.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "agent limits"
        verbose_name_plural = "agent limits"
        ordering = ("agent_key",)

    @property
    def label(self) -> str:
        return agent_label(self.agent_key)

    def __str__(self) -> str:
        return self.label
