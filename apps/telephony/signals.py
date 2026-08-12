from datetime import timedelta

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.patients.models import Patient, record_timeline_event

from .models import Call, CallbackTask

# Missed / RNR calls must land in the callback queue immediately (§1).
CALLBACK_SLA_MINUTES = 15


@receiver(post_save, sender=Call)
def resolve_patient_and_log_timeline(sender, instance: Call, created, **kwargs):
    if not created:
        return

    if instance.patient_id is None:
        match = Patient.objects.filter(hospital_id=instance.hospital_id, mobile=instance.from_number).first()
        if match is not None:
            instance.patient = match
            instance.save(update_fields=["patient"])

    if instance.patient_id is not None:
        record_timeline_event(
            patient=instance.patient,
            event_type="call",
            summary=f"{instance.get_direction_display()} call ({instance.get_status_display()}), {instance.duration_seconds}s",
            occurred_at=instance.started_at,
            source=instance,
        )

    if instance.status in (Call.Status.MISSED, Call.Status.RNR):
        CallbackTask.objects.create(
            hospital=instance.hospital,
            call=instance,
            patient=instance.patient,
            phone_number=instance.from_number,
            department=instance.department,
            sla_due_at=timezone.now() + timedelta(minutes=CALLBACK_SLA_MINUTES),
        )
