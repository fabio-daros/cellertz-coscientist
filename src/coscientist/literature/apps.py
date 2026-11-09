from django.apps import AppConfig


class LiteratureConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "coscientist.literature"
    label = "literature"
    verbose_name = "Literature search cache"
