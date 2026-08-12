import secrets
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.patients.models import record_timeline_event

from .models import Appointment, Slot, SlotTemplate, Waitlist

WEEKS_TO_GENERATE_AHEAD = 4


def generate_slots(template: SlotTemplate, *, weeks_ahead: int = WEEKS_TO_GENERATE_AHEAD) -> int:
    """Materializes concrete Slot rows from a recurring SlotTemplate for the
    next `weeks_ahead` weeks (§4). Idempotent — safe to re-run."""
    created = 0
    today = timezone.localdate()
    for offset in range(weeks_ahead * 7):
        day = today + timedelta(days=offset)
        if day.weekday() != template.weekday:
            continue

        current = datetime.combine(day, template.start_time)
        end = datetime.combine(day, template.end_time)
        step = timedelta(minutes=template.slot_duration_minutes)

        while current + step <= end:
            _, was_created = Slot.objects.get_or_create(
                hospital=template.hospital,
                doctor=template.doctor,
                date=day,
                start_time=current.time(),
                defaults={"end_time": (current + step).time()},
            )
            created += int(was_created)
            current += step

    return created


class SlotUnavailable(Exception):
    pass


@transaction.atomic
def book_appointment(*, patient, slot: Slot, source=Appointment.Source.CRM, reason="", booked_by=None) -> Appointment:
    """Clash-free booking: locks the slot row for the duration of the
    transaction so two simultaneous requests can't double-book it (§4)."""
    locked_slot = Slot.objects.select_for_update().get(pk=slot.pk)

    if locked_slot.is_blocked:
        raise SlotUnavailable("This slot has been blocked and is not bookable.")
    if Appointment.objects.filter(slot=locked_slot).exclude(
        status__in=[Appointment.Status.CANCELLED, Appointment.Status.RESCHEDULED]
    ).exists():
        raise SlotUnavailable("This slot is already booked.")

    return Appointment.objects.create(
        hospital=locked_slot.hospital,
        patient=patient,
        doctor=locked_slot.doctor,
        slot=locked_slot,
        source=source,
        reason=reason,
        booked_by=booked_by,
        registration_token=secrets.token_urlsafe(24),
    )


@transaction.atomic
def reschedule_appointment(appointment: Appointment, *, new_slot: Slot, changed_by=None) -> Appointment:
    appointment.status = Appointment.Status.RESCHEDULED
    appointment.cancelled_at = timezone.now()
    appointment.save(update_fields=["status", "cancelled_at"])

    new_appointment = book_appointment(
        patient=appointment.patient,
        slot=new_slot,
        source=appointment.source,
        reason=appointment.reason,
        booked_by=changed_by,
    )
    new_appointment.rescheduled_from = appointment
    new_appointment.save(update_fields=["rescheduled_from"])
    return new_appointment


def check_in(appointment: Appointment) -> Appointment:
    appointment.status = Appointment.Status.CHECKED_IN
    appointment.checked_in_at = timezone.now()
    appointment.save(update_fields=["status", "checked_in_at"])
    return appointment


def start_consult(appointment: Appointment) -> Appointment:
    appointment.status = Appointment.Status.IN_CONSULT
    appointment.save(update_fields=["status"])
    return appointment


def complete(appointment: Appointment) -> Appointment:
    from .signals import appointment_completed

    appointment.status = Appointment.Status.COMPLETED
    appointment.completed_at = timezone.now()
    appointment.save(update_fields=["status", "completed_at"])
    record_timeline_event(
        patient=appointment.patient,
        event_type="appointment",
        summary=f"Appointment with {appointment.doctor} completed",
        occurred_at=appointment.completed_at,
        source=appointment,
    )
    appointment_completed.send(sender=Appointment, appointment=appointment)
    return appointment


def cancel(appointment: Appointment) -> Appointment:
    appointment.status = Appointment.Status.CANCELLED
    appointment.cancelled_at = timezone.now()
    appointment.save(update_fields=["status", "cancelled_at"])
    try_fill_from_waitlist(appointment.slot)
    return appointment


def try_fill_from_waitlist(slot: Slot) -> Waitlist | None:
    """Offers a slot just freed by a cancellation to the longest-waiting
    matching Waitlist entry and notifies the patient (§4 waitlist
    auto-fill). No-ops if nobody is waiting for this doctor/department."""
    entry = (
        Waitlist.objects.filter(status=Waitlist.Status.WAITING, doctor=slot.doctor)
        .filter(Q(department__isnull=True) | Q(department=slot.doctor.department))
        .order_by("created_at")
        .first()
    )
    if entry is None:
        return None

    entry.offered_slot = slot
    entry.offered_at = timezone.now()
    entry.status = Waitlist.Status.OFFERED
    entry.save(update_fields=["offered_slot", "offered_at", "status"])

    from apps.communications.models import Channel
    from apps.communications.services import send_message

    send_message(
        patient=entry.patient,
        channel=Channel.WHATSAPP,
        purpose="waitlist_slot_offered",
        context={
            "patient_name": entry.patient.full_name,
            "doctor_name": str(entry.doctor),
            "date": slot.date.isoformat(),
            "time": slot.start_time.strftime("%H:%M"),
        },
        fallback_channel=Channel.SMS,
    )
    return entry


def mark_no_show(appointment: Appointment) -> Appointment:
    from .signals import appointment_no_show

    appointment.status = Appointment.Status.NO_SHOW
    appointment.no_show_at = timezone.now()
    appointment.save(update_fields=["status", "no_show_at"])
    appointment_no_show.send(sender=Appointment, appointment=appointment)
    return appointment
