from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

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
    preferred language (§4)."""
    from apps.communications.services import send_appointment_reminder

    now = timezone.now()
    # Slot.date/start_time are naive local wall-clock fields (the hospital's
    # own timezone), so the comparison window must be computed in local time
    # too — comparing against raw UTC now() silently misses every
    # appointment by TIME_ZONE's offset (e.g. 5:30 for Asia/Kolkata).
    local_now = timezone.localtime(now)
    sent = 0
    for offset_hours in settings.APPOINTMENT_REMINDER_OFFSETS_HOURS:
        field = "reminder_24h_sent_at" if offset_hours == 24 else "reminder_2h_sent_at"
        window_start = local_now + timedelta(hours=offset_hours)
        window_end = window_start + timedelta(minutes=15)

        due = Appointment.objects.filter(
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
    overdue = Appointment.objects.filter(
        status__in=OPEN_STATUSES,
        slot__date__lte=cutoff.date(),
    ).select_related("slot")

    marked = 0
    for appointment in overdue:
        naive_slot_end = timezone.datetime.combine(appointment.slot.date, appointment.slot.end_time)
        slot_end = timezone.make_aware(naive_slot_end) if timezone.is_naive(naive_slot_end) else naive_slot_end
        if slot_end < cutoff:
            mark_no_show(appointment)
            marked += 1
    return marked
