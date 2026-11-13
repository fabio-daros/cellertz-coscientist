"""Django-backed prompt store, read through the seam `cellertz` declares.

The science asks for a task body by agent key and knows nothing about where
it is kept. If this table holds no row for an agent, the built-in body is
used, so adding the web layer changed no prompt by itself.
"""

from __future__ import annotations

from django.db import close_old_connections

from coscientist.agents.models import AgentPrompt


class DjangoAgentPromptProvider:
    """The highest stored version wins. Absence means keep the built-in body."""

    def task_body(self, agent_key: str) -> tuple[int, str] | None:
        # The conversation service answers on a thread per request and is not
        # a Django request cycle, so nothing else closes the ORM's connection.
        close_old_connections()
        try:
            row = (
                AgentPrompt.objects.filter(agent_key=agent_key)
                .order_by("-version")
                .values_list("version", "body")
                .first()
            )
            if row is None:
                return None
            version, body = row
            return (int(version), body)
        finally:
            close_old_connections()
