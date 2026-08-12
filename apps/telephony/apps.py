from django.apps import AppConfig


class TelephonyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.telephony"

    def ready(self):
        from . import signals  # noqa: F401
