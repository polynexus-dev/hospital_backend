from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.patients.models import record_timeline_event

from .models import Enquiry
from .services import assign_enquiry, find_duplicates


@receiver(post_save, sender=Enquiry)
def on_enquiry_created(sender, instance: Enquiry, created, **kwargs):
    if not created:
        return

    if instance.duplicate_of_id is None:
        duplicate = find_duplicates(
            instance.hospital, mobile=instance.mobile, alternate_mobile=instance.alternate_mobile, email=instance.email
        ).exclude(pk=instance.pk).order_by("created_at").first()
        if duplicate is not None:
            instance.duplicate_of = duplicate
            instance.save(update_fields=["duplicate_of"])

    if instance.assigned_to_id is None:
        assign_enquiry(instance)

    if instance.patient_id is not None:
        record_timeline_event(
            patient=instance.patient,
            event_type="enquiry",
            summary=f"Enquiry from {instance.get_source_display()}: {instance.service_requested or 'general'}",
            occurred_at=instance.created_at or timezone.now(),
            source=instance,
        )
