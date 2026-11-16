"""Reduce the models offered to agents to a short, deliberate list.

The provider's catalogue is hundreds of models. Offering all of them makes
the choice harder, not freer, and puts an expensive model one mis-click away.
Rows are disabled rather than deleted, so the catalogue stays complete and a
model can be offered again without fetching it back.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from coscientist.agents.models import AgentLimitsConfig, AvailableModel


class Command(BaseCommand):
    help = "Offer only the named models to agents, and withdraw the rest."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--keep", default="", help="Comma-separated model ids to offer.")
        parser.add_argument("--also", default="", help="Comma-separated ids to offer as well as those already offered.")
        parser.add_argument("--yes", action="store_true", help="Apply. Without it, only report.")
        parser.add_argument(
            "--force",
            action="store_true",
            help="Withdraw a model even while an agent is configured with it.",
        )

    def handle(self, *args, **options) -> None:
        keep = self._ids(options["keep"])
        also = self._ids(options["also"])
        if not keep and not also:
            raise CommandError("name the models with --keep, or add to them with --also")

        known = set(AvailableModel.objects.values_list("model_id", flat=True))
        unknown = sorted((keep | also) - known)
        if unknown:
            raise CommandError(
                "not in the catalogue: " + ", ".join(unknown) + ". Run refresh_openrouter_models first."
            )

        if also:
            offer = set(AvailableModel.objects.filter(enabled=True).values_list("model_id", flat=True)) | also
        else:
            offer = keep

        # A model an agent is configured with would become "(retired)" on its
        # own row. That is a change to how an agent runs, not to a menu.
        in_use = {
            model_id
            for model_id in AgentLimitsConfig.objects.exclude(model="").values_list("model", flat=True)
        }
        losing_use = (in_use - offer) if not options["force"] else set()
        if losing_use:
            raise CommandError(
                "an agent is configured with: " + ", ".join(sorted(losing_use)) + ". Keep it, or pass --force."
            )

        withdraw = AvailableModel.objects.filter(enabled=True).exclude(model_id__in=offer)
        restore = AvailableModel.objects.filter(enabled=False, model_id__in=offer)
        self.stdout.write(f"offered after this: {len(offer)}")
        self.stdout.write(f"withdrawing       : {withdraw.count()}")
        self.stdout.write(f"offering again    : {restore.count()}")

        if not options["yes"]:
            self.stdout.write("Nothing changed. Run again with --yes.")
            return

        withdrawn = withdraw.update(enabled=False)
        restored = restore.update(enabled=True)
        self.stdout.write(f"withdrew {withdrawn}, offered again {restored}")
        for model_id in sorted(offer):
            self.stdout.write(f"  offered {model_id}")

    @staticmethod
    def _ids(raw: str) -> set[str]:
        return {item.strip() for item in raw.split(",") if item.strip()}
