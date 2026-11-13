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

    def __str__(self) -> str:
        return f"{self.agent_key} v{self.version}"
