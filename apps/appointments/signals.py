import django.dispatch
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.patients.models import record_timeline_event

from .models import Appointment

# Fired by apps.appointments.services.mark_no_show — apps.automation listens
# on this to create the auto-recall task instead of appointments importing
# automation directly (§4 "no-show ... automatic recall task").
appointment_no_show = django.dispatch.Signal()

# Fired by apps.appointments.services.complete — apps.feedback listens on
# this to send the post-visit feedback request (§10) without appointments
# needing to import feedback.
appointment_completed = django.dispatch.Signal()

# Fired by apps.appointments.services.check_in — apps.opd listens on this
# to create the clinical-content Encounter for the visit.
appointment_checked_in = django.dispatch.Signal()



@receiver(post_save, sender=Appointment)
def log_appointment_created(sender, instance: Appointment, created, **kwargs):
    if not created:
        return
    record_timeline_event(
        patient=instance.patient,
        event_type="appointment",
        summary=f"Appointment booked with {instance.doctor} on {instance.slot.date} {instance.slot.start_time}",
        occurred_at=instance.created_at,
        source=instance,
    )


@receiver(post_save, sender=Appointment)
def push_to_doctor_calendar(sender, instance: Appointment, **kwargs):
    """Booked, moved, cancelled: mirror it in the doctor's own calendar
    (apps.appointments.calendar_sync), after the booking has committed."""
    from django.db import transaction

    from .models import CalendarAppointmentEvent, CalendarConnection

    connected = CalendarConnection.objects.filter(doctor_id=instance.doctor_id, push_appointments=True).exists()
    if not connected and not CalendarAppointmentEvent.objects.filter(appointment_id=instance.pk).exists():
        return

    def dispatch():
        from .tasks import sync_appointment_to_calendar

        try:
            sync_appointment_to_calendar.delay(instance.pk)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Couldn't queue calendar sync for appointment %s", instance.pk)

    transaction.on_commit(dispatch)
