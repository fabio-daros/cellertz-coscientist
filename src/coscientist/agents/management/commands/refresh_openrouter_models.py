"""Refresh the models an agent may be given, from the provider's catalogue.

A deliberate command rather than a call from the admin: a page that renders
should not depend on a third party being reachable. Rows already stored are
updated, never removed, so a model that disappears from the catalogue keeps
whatever an agent was configured with and can be retired by hand.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from django.core.management.base import BaseCommand, CommandError

from coscientist.agents.models import AvailableModel

CATALOGUE = "https://openrouter.ai/api/v1/models"


class Command(BaseCommand):
    help = "Add the provider's models to the list an agent may be given."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--contains", default="", help="Only ids containing this text.")
        parser.add_argument("--timeout", type=float, default=20.0)

    def handle(self, *args, **options) -> None:
        payload = self._fetch(options["timeout"])
        entries = payload.get("data")
        if not isinstance(entries, list):
            raise CommandError("the catalogue did not answer with a list of models")

        wanted = options["contains"].strip().lower()
        added = updated = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            model_id = entry.get("id")
            if not isinstance(model_id, str) or "/" not in model_id:
                continue
            if wanted and wanted not in model_id.lower():
                continue
            label = entry.get("name") if isinstance(entry.get("name"), str) else ""
            # A new model arrives withdrawn: offering it is a person's choice, not the catalogue's.
            _row, created = AvailableModel.objects.update_or_create(
                model_id=model_id, defaults={"label": label}, create_defaults={"label": label, "enabled": False}
            )
            added, updated = (added + 1, updated) if created else (added, updated + 1)

        self.stdout.write(f"added {added}, updated {updated}, total {AvailableModel.objects.count()}")

    def _fetch(self, timeout: float) -> dict:
        request = urllib.request.Request(CATALOGUE, headers={"Accept": "application/json"})
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if key:
            request.add_header("Authorization", f"Bearer {key}")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            raise CommandError(f"the catalogue could not be read: {error}") from error
