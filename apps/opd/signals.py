from django.dispatch import receiver

from apps.appointments.signals import appointment_checked_in

from .models import Encounter


@receiver(appointment_checked_in)
def create_encounter_on_check_in(sender, appointment, **kwargs):
    """Every checked-in OPD appointment gets exactly one Encounter to hold
    its clinical content — see docs/erp/05-integration-architecture.md.
    get_or_create, not create: check_in() can run more than once for the
    same appointment in edge cases (e.g. a retried request), and this
    handler must stay idempotent rather than raise on the second call."""
    Encounter.objects.get_or_create(
        appointment=appointment,
        defaults={
            "hospital": appointment.hospital,
            "patient": appointment.patient,
            "doctor": appointment.doctor,
            "department": appointment.doctor.department,
        },
    )
