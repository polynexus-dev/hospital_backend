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
# to create the clinical-content Encounter for the visit (see
# docs/erp/05-integration-architecture.md). Deliberately fired at check-in,
# not at completion like appointment_completed above: a doctor needs
# somewhere to record vitals/notes/diagnosis *during* the consultation,
# before it's marked complete.
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
