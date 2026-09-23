from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.dispatch import receiver
from django.utils import timezone

from apps.appointments.signals import appointment_no_show

from .models import Task

# How soon the recall task falls due after a no-show is recorded (§4/§6).
RECALL_TASK_DUE_HOURS = 24


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


try:
    from apps.laboratory.signals import result_critical

    @receiver(result_critical)
    def create_critical_lab_result_task(sender, result, **kwargs):
        Task.objects.create(
            hospital=result.hospital,
            title=f"URGENT: Critical Lab Result for {result.lab_order.patient}",
            description=f"Critical lab result for test {result.lab_test}: {result.value}",
            priority=Task.Priority.URGENT,
            content_type=ContentType.objects.get_for_model(result),
            object_id=result.pk,
        )
except ImportError:
    pass

