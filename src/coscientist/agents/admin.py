import json

from django import forms
from django.contrib import admin
from django.utils.safestring import mark_safe

from cellertz.application.agent_prompt_references import referenced_versions
from cellertz.application.agent_prompts import builtin_body, known_agents
from cellertz.application.agent_runtime import default_limits
from coscientist.agents.models import AgentLimitsConfig, AgentPrompt


class PromptBodyWidget(forms.Textarea):
    """A textarea that follows the agent chosen above it.

    The swap is server-rendered data plus a few lines of script rather than a
    collected static file, so the admin needs no deployment step of its own.
    A text the editor has changed is never replaced: losing an edit to a
    dropdown would be its own defect.
    """

    def render(self, name, value, attrs=None, renderer=None):
        textarea = super().render(name, value, attrs, renderer)
        bodies = self.attrs.get("data-bodies")
        if not bodies:
            return textarea
        element_id = (attrs or {}).get("id", f"id_{name}")
        script = f"""
<script>
(function () {{
  var box = document.getElementById({json.dumps(element_id)});
  var picker = document.getElementById("id_agent_key");
  if (!box || !picker) return;
  var bodies = JSON.parse(box.getAttribute("data-bodies"));
  var untouched = box.value;
  box.addEventListener("input", function () {{ untouched = null; }});
  picker.addEventListener("change", function () {{
    var next = bodies[picker.value];
    if (next === undefined) return;
    if (untouched === null && box.value.trim() !== "") {{
      if (!window.confirm("Replace the text you edited with this agent's own?")) return;
    }}
    box.value = next;
    untouched = next;
  }});
}})();
</script>"""
        return mark_safe(textarea + script)


class AgentPromptForm(forms.ModelForm):
    """Only a registered agent may be chosen, and the next version is offered.

    A free-text key would save a row that is never sent, and a version that
    collides is refused by the database rather than explained here.
    """

    class Meta:
        model = AgentPrompt
        fields = ("agent_key", "version", "body", "note")
        widgets = {"body": PromptBodyWidget(attrs={"rows": 12})}

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
        self.fields["body"].initial = self.text_in_force(chosen)
        # Changing the agent must change the text with it. The page is rendered
        # once, so the alternatives travel with it and the widget swaps them.
        self.fields["body"].widget.attrs["data-bodies"] = json.dumps(
            {key: self.text_in_force(key) for key, _label in agents}
        )

    @staticmethod
    def text_in_force(agent_key: str) -> str:
        """The stored body if there is one, otherwise the code's own."""

        stored = (
            AgentPrompt.objects.filter(agent_key=agent_key)
            .order_by("-version")
            .values_list("body", flat=True)
            .first()
        )
        return stored if stored is not None else (builtin_body(agent_key) or "")

    def clean(self):
        """Refuse a body that belongs to a different agent.

        The agent and the text are two fields, and nothing stops them being
        filled from different agents — a page rendered for one agent and then
        switched to another would save the wrong prompt under the wrong name,
        and the analysis recorded against it would say so for ever.
        """

        cleaned = super().clean()
        agent_key = cleaned.get("agent_key")
        body = (cleaned.get("body") or "").strip()
        if not agent_key or not body:
            return cleaned
        for key, label in known_agents():
            if key == agent_key:
                continue
            if body == (builtin_body(key) or "").strip():
                raise forms.ValidationError(
                    {
                        "body": (
                            f"This is the built-in text of {label}, not of the agent selected above. "
                            "Choose the agent first, then edit its text."
                        )
                    }
                )
        return cleaned


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
        """Deletable only while no recorded result names this version.

        The blanket refusal this replaces was honest about the risk and blind
        about the case: most versions are never referenced, and a row made by
        mistake had to be removed from a shell.

        Bulk deletion stays off. Each row is checked against the recorded
        results, and that is a decision to take one at a time.
        """

        if obj is None:
            return False
        return (obj.agent_key, obj.version) not in referenced_versions()


class AgentDefaultsSelect(forms.Select):
    """A picker that rewrites the limit placeholders when the agent changes.

    Each agent spends differently, so the number an empty box falls back to
    differs too. Showing one agent's defaults beside another's name is the
    same confusion the prompt body had.
    """

    def __init__(self, choices, defaults) -> None:
        super().__init__(choices=choices)
        self.defaults = defaults

    def render(self, name, value, attrs=None, renderer=None):
        select = super().render(name, value, attrs, renderer)
        script = f"""
<script>
(function () {{
  var defaults = {json.dumps(self.defaults)};
  var picker = document.getElementById("id_agent_key");
  if (!picker) return;
  function apply() {{
    var chosen = defaults[picker.value] || {{}};
    Object.keys(chosen).forEach(function (field) {{
      var box = document.getElementById("id_" + field);
      if (!box) return;
      box.setAttribute("placeholder", "default: " + chosen[field]);
      var help = box.parentNode.querySelector(".help, .helptext");
      if (help) help.textContent = help.textContent.replace(
        /Leave blank for [^.]*\\./, "Leave blank for " + chosen[field] + ".");
    }});
  }}
  picker.addEventListener("change", apply);
  apply();
}})();
</script>"""
        return mark_safe(select + script)


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
        agents = known_agents()
        first = agents[0][0] if agents else ""
        # The browser shows the first option when nothing is selected, so the
        # form must assume the same agent. Otherwise the page would offer no
        # default at all, while appearing to have an agent chosen.
        chosen = (
            getattr(self.instance, "agent_key", "")
            or self.data.get("agent_key")
            or self.initial.get("agent_key")
            or first
        )
        defaults = default_limits(chosen)
        if defaults is None:
            return
        # A blank field keeps the default, so the default is what the empty
        # box should read: nobody should have to open the code to see it.
        for name, value in defaults.to_dict().items():
            if name in self.fields:
                self.fields[name].widget.attrs["placeholder"] = f"default: {value}"
                # A number input is narrow by default and clipped the hint to
                # "default:", which told the reader nothing.
                self.fields[name].widget.attrs["style"] = "width: 12em;"
                self.fields[name].help_text = f"{self.fields[name].help_text} Leave blank for {value}."
        # And the defaults must follow the dropdown, as the prompt body does.
        self.fields["agent_key"].widget = AgentDefaultsSelect(
            choices=list(agents),
            defaults={key: (default_limits(key).to_dict() if default_limits(key) else {}) for key, _ in agents},
        )


@admin.register(AgentLimitsConfig)
class AgentLimitsAdmin(admin.ModelAdmin):
    form = AgentLimitsForm
    list_display = ("agent_key", "max_output_tokens", "max_model_calls", "token_budget", "call_timeout_seconds", "updated_at")
    ordering = ("agent_key",)

    def get_readonly_fields(self, request, obj=None):
        # The agent a row belongs to is not something an edit should move.
        return ("agent_key",) if obj else ()
