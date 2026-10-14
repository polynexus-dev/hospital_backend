"""Missed-opportunity tracking: calls that should have turned into a visit
and didn't.

- unreturned: an inbound call that was missed (or the caller hung up in the
  queue) and nobody spoke to that number within 24 hours;
- not_booked: an answered call where the caller wanted to book (from the AI
  analysis, apps.telephony.call_ai) and no appointment or scheduled enquiry
  followed within 48 hours.

flag_missed_opportunities runs hourly; the Calls > Missed opportunities
report reads the flags.
"""
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from .models import Call, CallbackTask

RETURN_WINDOW = timedelta(hours=24)
BOOKING_WINDOW = timedelta(hours=48)
LOOKBACK = timedelta(days=14)
UNANSWERED = [Call.Status.MISSED, Call.Status.RNR, Call.Status.BUSY, Call.Status.VOICEMAIL]
BOOKED_STAGES = ["scheduled", "visited", "completed"]


def _last10(number):
    digits = "".join(ch for ch in number or "" if ch.isdigit())
    return digits[-10:]


def _spoke_to_number(call):
    number = _last10(call.from_number)
    if not number:
        return True  # withheld number: nothing anyone could have done
    window = (call.started_at, call.started_at + RETURN_WINDOW)
    answered = Call.objects.filter(hospital_id=call.hospital_id, status=Call.Status.ANSWERED, started_at__gt=window[0],
                                   started_at__lte=window[1]).filter(Q(from_number__endswith=number) | Q(to_number__endswith=number))
    resolved = CallbackTask.objects.filter(hospital_id=call.hospital_id, phone_number__endswith=number, status=CallbackTask.Status.DONE,
                                           resolved_at__gt=window[0], resolved_at__lte=window[1])
    return answered.exists() or resolved.exists()


def _booked_after(call):
    from apps.appointments.models import Appointment
    from apps.enquiries.services import find_duplicates
    from apps.patients.models import Patient

    until = call.started_at + BOOKING_WINDOW
    patient = call.patient or Patient.objects.filter(hospital_id=call.hospital_id).by_mobile(call.from_number).first()
    if patient is not None and Appointment.objects.filter(patient=patient, created_at__gte=call.started_at, created_at__lte=until).exists():
        return True
    from apps.enquiries.models import Enquiry

    matched = find_duplicates(call.hospital, mobile=call.from_number).filter(stage__in=BOOKED_STAGES).exists()
    return matched or bool(call.enquiry_id and Enquiry.objects.filter(pk=call.enquiry_id, stage__in=BOOKED_STAGES).exists())


def flag_missed_opportunities(now=None):
    now = now or timezone.now()
    flagged = 0
    unanswered = Call.objects.filter(direction=Call.Direction.INBOUND, status__in=UNANSWERED, missed_opportunity="",
                                     started_at__gte=now - LOOKBACK, started_at__lte=now - RETURN_WINDOW)
    for call in unanswered.iterator():
        if not _spoke_to_number(call):
            Call.objects.filter(pk=call.pk).update(missed_opportunity=Call.MissedOpportunity.UNRETURNED, missed_opportunity_at=now)
            flagged += 1
    wanted_booking = Call.objects.filter(status=Call.Status.ANSWERED, ai_booking_intent=True, missed_opportunity="",
                                         started_at__gte=now - LOOKBACK, started_at__lte=now - BOOKING_WINDOW).select_related("hospital")
    for call in wanted_booking.iterator():
        if not _booked_after(call):
            Call.objects.filter(pk=call.pk).update(missed_opportunity=Call.MissedOpportunity.NOT_BOOKED, missed_opportunity_at=now)
            flagged += 1
    return flagged


def report(calls, start, end):
    """calls: the hospital's (already tenant-scoped) Call queryset."""
    window = calls.filter(started_at__gte=start, started_at__lt=end)
    flagged = window.exclude(missed_opportunity="").select_related("patient", "department", "operator").order_by("-started_at")
    by_reason = dict(flagged.values_list("missed_opportunity").annotate(n=Count("id")))
    by_class = dict(window.exclude(ai_lead_class="").values_list("ai_lead_class").annotate(n=Count("id")))
    analysed = window.filter(ai_status=Call.AIStatus.DONE)
    return {
        "from": start.date().isoformat(),
        "to": (end - timedelta(days=1)).date().isoformat(),
        "summary": {
            "missed_opportunities": flagged.count(),
            "unreturned": by_reason.get(Call.MissedOpportunity.UNRETURNED, 0),
            "not_booked": by_reason.get(Call.MissedOpportunity.NOT_BOOKED, 0),
            "analysed_calls": analysed.count(),
            "booking_intent_calls": analysed.filter(ai_booking_intent=True).count(),
            "lead_classes": {c.value: by_class.get(c.value, 0) for c in Call.LeadClass},
        },
        "flagged": flagged[:300],
    }
