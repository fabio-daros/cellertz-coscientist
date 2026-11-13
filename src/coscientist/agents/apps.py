from django.apps import AppConfig


class AgentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "coscientist.agents"
    label = "agents"

    def ready(self) -> None:
        # The admin lists the agents that read a stored prompt, and an agent
        # registers itself when its module is imported. The admin process
        # serves Django alone and would otherwise never import the science,
        # so the list would be empty and no prompt could be added.
        from cellertz.application import request_supervisor  # noqa: F401
