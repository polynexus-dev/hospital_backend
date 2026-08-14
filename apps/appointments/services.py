import secrets
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from apps.patients.models import record_timeline_event

from .models import Appointment, Doctor, Slot, SlotTemplate, Waitlist

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


@transaction.atomic
def check_in(appointment: Appointment) -> Appointment:
    """Checking in also assigns the walk-in-style OPD queue token — the
    "now serving #N" number front desk/waiting-room displays use, distinct
    from the pre-booked slot time so same-day check-ins queue in arrival
    order regardless of when their slot was booked for. select_for_update
    here mirrors book_appointment's locking so two simultaneous check-ins
    for the same doctor/day can't land on the same token."""
    from .signals import appointment_checked_in

    appointment.status = Appointment.Status.CHECKED_IN
    appointment.checked_in_at = timezone.now()

    if appointment.queue_token is None:
        todays_tokens = (
            Appointment.objects.select_for_update()
            .filter(doctor=appointment.doctor, slot__date=appointment.slot.date, queue_token__isnull=False)
            .aggregate(highest=Max("queue_token"))
        )
        appointment.queue_token = (todays_tokens["highest"] or 0) + 1
        appointment.save(update_fields=["status", "checked_in_at", "queue_token"])
    else:
        appointment.save(update_fields=["status", "checked_in_at"])
    appointment_checked_in.send(sender=Appointment, appointment=appointment)
    return appointment


def doctor_queue(doctor: Doctor, date) -> dict:
    """Front-desk/waiting-room queue view for a doctor on a given date:
    who's checked in and waiting, who's currently in consult (now serving),
    ordered by queue token / arrival."""
    appointments = (
        Appointment.objects.filter(doctor=doctor, slot__date=date, queue_token__isnull=False)
        .select_related("patient", "slot")
        .order_by("queue_token")
    )
    now_serving = appointments.filter(status=Appointment.Status.IN_CONSULT).first()
    waiting = appointments.filter(status=Appointment.Status.CHECKED_IN)
    return {"now_serving": now_serving, "waiting": list(waiting)}


@transaction.atomic
def block_doctor_slots(doctor: Doctor, *, start_date, end_date, reason: str = "") -> dict:
    """Bulk-blocks a doctor's slots over a date range (leave, OT block,
    conference) in one call instead of one row at a time. Only touches
    currently-unbooked slots — already-booked ones are left alone and
    counted separately so front desk knows how many patients still need a
    manual reschedule/callback."""
    slots = Slot.objects.select_for_update().filter(doctor=doctor, date__gte=start_date, date__lte=end_date)
    bookable = slots.select_related("appointment").filter(is_blocked=False)

    blocked_ids, booked_ids = [], []
    for slot in bookable:
        if slot.is_booked:
            booked_ids.append(slot.id)
        else:
            blocked_ids.append(slot.id)

    Slot.objects.filter(id__in=blocked_ids).update(is_blocked=True, blocked_reason=reason)
    return {"blocked": len(blocked_ids), "skipped_already_booked": len(booked_ids), "booked_slot_ids": booked_ids}


def unblock_doctor_slots(doctor: Doctor, *, start_date, end_date) -> int:
    """Reverses block_doctor_slots — e.g. a doctor's leave is cancelled."""
    return Slot.objects.filter(
        doctor=doctor, date__gte=start_date, date__lte=end_date, is_blocked=True
    ).update(is_blocked=False, blocked_reason="")


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
