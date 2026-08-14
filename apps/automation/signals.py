from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.dispatch import receiver
from django.utils import timezone

from apps.appointments.signals import appointment_no_show
from apps.laboratory.signals import result_critical

from .models import Task

# How soon the recall task falls due after a no-show is recorded (§4/§6).
RECALL_TASK_DUE_HOURS = 24

# A critical lab value needs eyes on it fast — much tighter window than a
# routine recall task above.
CRITICAL_RESULT_ALERT_DUE_HOURS = 1


@receiver(appointment_no_show)
def create_no_show_recall_task(sender, appointment, **kwargs):
    Task.objects.create(
        hospital=appointment.hospital,
        title=f"Recall {appointment.patient} — missed {appointment.doctor} appointment",
        description=f"No-show for the {appointment.slot.date} {appointment.slot.start_time} slot. Call to reschedule.",
        department=appointment.doctor.department,
        due_at=timezone.now() + timedelta(hours=RECALL_TASK_DUE_HOURS),
        priority=Task.Priority.HIGH,
        content_type=ContentType.objects.get_for_model(appointment),
        object_id=appointment.pk,
    )


@receiver(result_critical)
def create_critical_lab_result_alert_task(sender, result, **kwargs):
    lab_order = result.lab_order
    reference_range = result.reference_range or result.lab_test.reference_range
    Task.objects.create(
        hospital=result.hospital,
        title=f"CRITICAL lab result — {lab_order.patient}: {result.lab_test}",
        description=f"Value: {result.value} {result.unit} (reference: {reference_range}).",
        due_at=timezone.now() + timedelta(hours=CRITICAL_RESULT_ALERT_DUE_HOURS),
        priority=Task.Priority.URGENT,
        content_type=ContentType.objects.get_for_model(result),
        object_id=result.pk,
    )
