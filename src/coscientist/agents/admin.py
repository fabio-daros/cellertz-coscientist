from django import forms
from django.contrib import admin

from cellertz.application.agent_prompts import builtin_body, known_agents
from coscientist.agents.models import AgentPrompt


class AgentPromptForm(forms.ModelForm):
    """Only a registered agent may be chosen, and the next version is offered.

    A free-text key would save a row that is never sent, and a version that
    collides is refused by the database rather than explained here.
    """

    class Meta:
        model = AgentPrompt
        fields = ("agent_key", "version", "body", "note")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        agents = known_agents()
        self.fields["agent_key"] = forms.ChoiceField(
            choices=[(key, key) for key, _ in agents],
            label="Agent",
            help_text="Only agents that read a stored prompt are listed.",
        )
        if self.instance.pk:
            return
        first = agents[0][0] if agents else ""
        chosen = (self.data.get("agent_key") or self.initial.get("agent_key") or first) or ""
        highest = (
            AgentPrompt.objects.filter(agent_key=chosen).order_by("-version").values_list("version", flat=True).first()
        )
        self.fields["version"].initial = (highest or 0) + 1
        # The body in force right now, so a new version is an edit of the real
        # text rather than something typed from memory.
        self.fields["body"].initial = builtin_body(chosen) if highest is None else self._body_of(chosen, highest)

    @staticmethod
    def _body_of(agent_key: str, version: int) -> str:
        return AgentPrompt.objects.filter(agent_key=agent_key, version=version).values_list("body", flat=True).first() or ""


@admin.register(AgentPrompt)
class AgentPromptAdmin(admin.ModelAdmin):
    form = AgentPromptForm
    list_display = ("agent_key", "version", "created_at")
    list_filter = ("agent_key",)
    search_fields = ("agent_key", "body", "note")
    ordering = ("agent_key", "-version")

    def get_readonly_fields(self, request, obj=None):
        # A saved version is what a recorded analysis points at, so it is read
        # only from here on. Changing the prompt means adding a version.
        return ("agent_key", "version", "body", "note", "created_at") if obj else ()

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
