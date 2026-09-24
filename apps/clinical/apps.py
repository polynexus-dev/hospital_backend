from django.apps import AppConfig


class ClinicalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.clinical"
    verbose_name = "Clinical Documentation & Decision Support"

    def ready(self):
        from . import signals  # noqa: F401
        from apps.ipd.workflow import connect_signals

        connect_signals()
