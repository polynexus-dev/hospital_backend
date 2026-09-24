from django.apps import AppConfig


class SchemesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.schemes"
    verbose_name = "Government health schemes"

    def ready(self):
        from . import signals  # noqa: F401
