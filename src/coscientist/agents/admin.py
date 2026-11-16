from django import forms
from django.contrib import admin

from cellertz.application.agent_prompts import builtin_body, known_agents
from cellertz.application.agent_runtime import default_limits
from coscientist.agents.models import AgentLimitsConfig, AgentPrompt


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
            choices=list(agents),
            label="Agent and task",
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


class AgentLimitsForm(forms.ModelForm):
    """Only a registered agent, and the code's defaults shown as placeholders."""

    class Meta:
        model = AgentLimitsConfig
        fields = ("agent_key", "max_output_tokens", "max_model_calls", "token_budget", "call_timeout_seconds", "note")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["agent_key"] = forms.ChoiceField(
            choices=list(known_agents()),
            label="Agent and task",
            help_text="Only agents whose limits the code reads are listed.",
        )
        chosen = (
            getattr(self.instance, "agent_key", "")
            or self.data.get("agent_key")
            or self.initial.get("agent_key")
            or ""
        )
        defaults = default_limits(chosen)
        if defaults is None:
            return
        # A blank field keeps the default, so the default is what the empty
        # box should read: nobody should have to open the code to see it.
        for name, value in defaults.to_dict().items():
            if name in self.fields:
                self.fields[name].widget.attrs["placeholder"] = f"default: {value}"
                self.fields[name].help_text = f"{self.fields[name].help_text} Leave blank for {value}."


@admin.register(AgentLimitsConfig)
class AgentLimitsAdmin(admin.ModelAdmin):
    form = AgentLimitsForm
    list_display = ("agent_key", "max_output_tokens", "max_model_calls", "token_budget", "call_timeout_seconds", "updated_at")
    ordering = ("agent_key",)

    def get_readonly_fields(self, request, obj=None):
        # The agent a row belongs to is not something an edit should move.
        return ("agent_key",) if obj else ()
