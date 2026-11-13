from django.contrib import admin

from coscientist.agents.models import AgentPrompt


@admin.register(AgentPrompt)
class AgentPromptAdmin(admin.ModelAdmin):
    list_display = ("agent_key", "version", "created_at")
    list_filter = ("agent_key",)
    search_fields = ("agent_key", "body", "note")
    ordering = ("agent_key", "-version")

    def get_readonly_fields(self, request, obj=None):
        # A saved version is what a recorded analysis points at, so it is read
        # only from here on. Changing the prompt means adding a version.
        return ("agent_key", "version", "body", "note", "created_at") if obj else ("created_at",)

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
