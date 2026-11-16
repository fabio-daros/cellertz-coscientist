"""Remove stored prompt versions that no recorded result names.

Deleting a version a result was written under would leave that result
unexplainable, so those are refused by name rather than by a blanket rule.
Nothing is removed without --yes.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from cellertz.application.agent_prompt_references import referenced_versions
from coscientist.agents.models import AgentPrompt


class Command(BaseCommand):
    help = "Delete stored agent prompt versions that no analysis or suggestion references."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--agent", default="", help="Only this agent key.")
        parser.add_argument(
            "--prompt-version",
            type=int,
            default=None,
            help="Only this version. Django owns --version, so the name is spelled out.",
        )
        parser.add_argument("--yes", action="store_true", help="Delete. Without it, only report.")

    def handle(self, *args, **options) -> None:
        rows = AgentPrompt.objects.all().order_by("agent_key", "version")
        if options["agent"]:
            rows = rows.filter(agent_key=options["agent"])
        if options["prompt_version"] is not None:
            rows = rows.filter(version=options["prompt_version"])

        referenced = referenced_versions()
        keep, removable = [], []
        for row in rows:
            (keep if (row.agent_key, row.version) in referenced else removable).append(row)

        for row in keep:
            self.stdout.write(f"kept      {row.agent_key} v{row.version} — a recorded result names it")
        for row in removable:
            self.stdout.write(f"removable {row.agent_key} v{row.version} — nothing names it")

        if not removable:
            self.stdout.write("nothing to remove")
            return
        if not options["yes"]:
            self.stdout.write(f"{len(removable)} would be deleted. Run again with --yes.")
            return

        for row in removable:
            self.stdout.write(f"deleted   {row.agent_key} v{row.version}")
            row.delete()
