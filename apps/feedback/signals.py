from datetime import timedelta

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.appointments.signals import appointment_completed
from apps.patients.models import record_timeline_event

from .models import FeedbackRequest, NPSResponse, ServiceRecoveryTask

# Detractors must be reached before they post publicly (§10).
SERVICE_RECOVERY_SLA_HOURS = 4


@receiver(appointment_completed)
def request_feedback_after_visit(sender, appointment, **kwargs):
    from .services import send_feedback_request

    feedback_request = FeedbackRequest.objects.create(
        hospital=appointment.hospital,
        patient=appointment.patient,
        appointment=appointment,
        doctor=appointment.doctor,
        department=appointment.doctor.department,
    )
    send_feedback_request(feedback_request)


@receiver(post_save, sender=NPSResponse)
def on_nps_response(sender, instance: NPSResponse, created, **kwargs):
    if not created:
        return

    FeedbackRequest.objects.filter(pk=instance.feedback_request_id).update(status=FeedbackRequest.Status.RESPONDED)

    record_timeline_event(
        patient=instance.patient,
        event_type="feedback",
        summary=f"NPS score {instance.score} ({instance.category})",
        occurred_at=instance.created_at,
        source=instance,
    )

    if instance.category == NPSResponse.Category.DETRACTOR:
        ServiceRecoveryTask.objects.create(
            hospital=instance.hospital,
            nps_response=instance,
            sla_due_at=timezone.now() + timedelta(hours=SERVICE_RECOVERY_SLA_HOURS),
        )
