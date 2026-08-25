from datetime import timedelta
from zoneinfo import ZoneInfo

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from apps.core.models import Hospital

from .models import Appointment
from .services import mark_no_show

# Grace period after a slot's end time before an un-checked-in appointment
# is treated as a no-show (§4).
NO_SHOW_GRACE_MINUTES = 30

OPEN_STATUSES = [Appointment.Status.BOOKED, Appointment.Status.CONFIRMED]


@shared_task
def send_appointment_reminders():
    """Runs frequently on Celery beat. Sends the 24h/2h reminders configured
    in settings.APPOINTMENT_REMINDER_OFFSETS_HOURS, in the patient's
    preferred language (§4).

    Looped per active hospital (not one global query) for two reasons:
    tenant-safety (a suspended hospital's appointments must stop being
    processed, matching apps.core.permissions.HospitalActive's lockout —
    the old single unscoped query didn't check is_active at all), and
    correctness (Slot.date/start_time are each hospital's own local
    wall-clock fields, so "now" has to be converted using *that* hospital's
    Hospital.timezone, not the Celery worker's single default TIME_ZONE —
    comparing against one global local time silently misses every
    appointment by the offset difference for a hospital in another zone)."""
    from apps.communications.services import send_appointment_reminder

    now = timezone.now()
    sent = 0
    for hospital in Hospital.objects.filter(is_active=True):
        local_now = now.astimezone(ZoneInfo(hospital.timezone))
        for offset_hours in settings.APPOINTMENT_REMINDER_OFFSETS_HOURS:
            field = "reminder_24h_sent_at" if offset_hours == 24 else "reminder_2h_sent_at"
            window_start = local_now + timedelta(hours=offset_hours)
            window_end = window_start + timedelta(minutes=15)

            due = Appointment.objects.filter(
                hospital=hospital,
                status__in=OPEN_STATUSES,
                **{f"{field}__isnull": True},
                slot__date=window_start.date(),
                slot__start_time__gte=window_start.time(),
                slot__start_time__lt=window_end.time(),
            )
            for appointment in due:
                send_appointment_reminder(appointment, offset_hours=offset_hours)
                setattr(appointment, field, now)
                appointment.save(update_fields=[field])
                sent += 1
    return sent


@shared_task
def mark_overdue_appointments_as_no_show():
    cutoff = timezone.now() - timedelta(minutes=NO_SHOW_GRACE_MINUTES)
    marked = 0
    for hospital in Hospital.objects.filter(is_active=True):
        overdue = Appointment.objects.filter(
            hospital=hospital,
            status__in=OPEN_STATUSES,
            slot__date__lte=cutoff.date(),
        ).select_related("slot")

        for appointment in overdue:
            naive_slot_end = timezone.datetime.combine(appointment.slot.date, appointment.slot.end_time)
            slot_end = timezone.make_aware(naive_slot_end) if timezone.is_naive(naive_slot_end) else naive_slot_end
            if slot_end < cutoff:
                mark_no_show(appointment)
                marked += 1
    return marked
