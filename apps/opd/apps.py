from django.apps import AppConfig


class OpdConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.opd"

    def ready(self):
        from . import signals  # noqa: F401
