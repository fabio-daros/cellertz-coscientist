from django.contrib import admin

from coscientist.literature.models import LiteratureSearchCacheEntry


@admin.register(LiteratureSearchCacheEntry)
class LiteratureSearchCacheEntryAdmin(admin.ModelAdmin):
    list_display = ("query", "stored_at")
    search_fields = ("query",)
    readonly_fields = ("query", "stored_at", "payload")
    ordering = ("-stored_at",)
