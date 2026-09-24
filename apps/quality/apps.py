from django.apps import AppConfig
from django.db.models.signals import post_save


class QualityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.quality"
    verbose_name = "Patient Safety & Quality"

    def ready(self):
        from apps.core.models import Hospital

        def seed(sender, instance, created, **kwargs):
            if created:
                from .defaults import seed_hospital

                seed_hospital(instance)

        post_save.connect(seed, sender=Hospital, dispatch_uid="quality_seed_hospital", weak=False)
