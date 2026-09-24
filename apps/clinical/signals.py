from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.core.models import Hospital


@receiver(post_save, sender=Hospital)
def seed_clinical_defaults(sender, instance, created, **kwargs):
    if created:
        from apps.icu.workflow import seed_criteria

        from .defaults import seed_hospital

        seed_hospital(instance)
        seed_criteria(instance)
        from apps.dietary.views import seed_diets
        from apps.oncology.views import seed_protocols

        seed_diets(instance)
        seed_protocols(instance)


@receiver(post_save, sender="opd.Diagnosis")
def diagnosis_notifiable_check(sender, instance, created, **kwargs):
    """COP.12.c — every recorded diagnosis is screened against the
    hospital's notifiable-disease list."""
    if created:
        from .services import check_notifiable

        check_notifiable(instance.encounter.patient, instance.description or "", instance.icd_code or "")
