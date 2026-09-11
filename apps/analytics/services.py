from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone

from django.contrib.contenttypes.models import ContentType

from apps.appointments.models import Appointment
from apps.automation.models import Task
from apps.communications.models import Message
from apps.enquiries.models import Enquiry
from apps.integrations.models import HISBillingRecord
from apps.telephony.models import Call, CallbackTask

# Free-text Template.purpose codes for the automated appointment-reminder
# sends (see apps.communications.models.Template docstring).
APPOINTMENT_REMINDER_PURPOSES = ["appointment_reminder_24h", "appointment_reminder_2h"]


def _today_range():
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def call_performance(hospital, start, end):
    """§1/§12 — received / answered / lost / average wait."""
    calls = Call.objects.filter(hospital=hospital, started_at__gte=start, started_at__lt=end)
    return calls.aggregate(
        received=Count("id"),
        answered=Count("id", filter=Q(status=Call.Status.ANSWERED)),
        missed=Count("id", filter=Q(status__in=[Call.Status.MISSED, Call.Status.RNR])),
        avg_duration_seconds=Avg("duration_seconds", filter=Q(status=Call.Status.ANSWERED)),
    )


def enquiry_funnel(hospital, start, end):
    """§2/§12 — pipeline counts by stage for the window."""
    enquiries = Enquiry.objects.filter(hospital=hospital, created_at__gte=start, created_at__lt=end)
    counts = enquiries.values("stage").annotate(count=Count("id"))
    return {row["stage"]: row["count"] for row in counts}


def department_doctor_volume(hospital, start, end):
    """§12 — appointment volume and completion rate by department/doctor."""
    appointments = Appointment.objects.filter(hospital=hospital, created_at__gte=start, created_at__lt=end)
    rows = appointments.values("doctor__name", "doctor__department__name").annotate(
        booked=Count("id"),
        completed=Count("id", filter=Q(status=Appointment.Status.COMPLETED)),
        no_show=Count("id", filter=Q(status=Appointment.Status.NO_SHOW)),
    )
    return list(rows)


def no_show_recall_effectiveness(hospital, start, end):
    """§12 — of the no-shows in the window, how many recall tasks
    (auto-created by apps.automation on the no-show signal) got closed."""
    no_shows = Appointment.objects.filter(hospital=hospital, no_show_at__gte=start, no_show_at__lt=end)
    no_show_ids = list(no_shows.values_list("id", flat=True))
    if not no_show_ids:
        return {"no_shows": 0, "recall_tasks_done": 0}

    appointment_content_type = ContentType.objects.get_for_model(Appointment)
    recall_tasks = Task.objects.filter(hospital=hospital, content_type=appointment_content_type, object_id__in=no_show_ids)
    return {
        "no_shows": len(no_show_ids),
        "recall_tasks_done": recall_tasks.filter(status=Task.Status.DONE).count(),
    }


def daily_mis_summary(hospital, *, start=None, end=None):
    """§12 — the numbers that go into the owner's daily WhatsApp MIS:
    calls, enquiries, conversions, no-shows, footfall by source."""
    if start is None or end is None:
        start, end = _today_range()

    calls = call_performance(hospital, start, end)
    funnel = enquiry_funnel(hospital, start, end)
    no_show = no_show_recall_effectiveness(hospital, start, end)

    appointments = Appointment.objects.filter(hospital=hospital, created_at__gte=start, created_at__lt=end)
    completed_by_source = appointments.filter(status=Appointment.Status.COMPLETED).values("source").annotate(count=Count("id"))

    pending_callbacks = CallbackTask.objects.filter(
        hospital=hospital, status__in=[CallbackTask.Status.PENDING, CallbackTask.Status.ESCALATED]
    ).count()

    return {
        "date": start.date().isoformat(),
        "calls": calls,
        "enquiry_funnel": funnel,
        "no_show": no_show,
        "footfall_by_source": {row["source"]: row["count"] for row in completed_by_source},
        "pending_callbacks": pending_callbacks,
        "note": "Revenue attribution pending HIS billing sync — see apps.integrations.",
    }


def revenue_by_source(hospital, start, end):
    """§3/§12 (extension) — enquiry volume, conversion and billed revenue
    by lead source. There is no direct enquiry↔bill link coming out of the
    HIS feed (apps.integrations), so billed_amount is joined via patient:
    only the patient's *first* in-window enquiry counts toward a source,
    and every HIS bill dated on/after that enquiry is credited to it. See
    the "note" in the return value."""
    enquiries = Enquiry.objects.filter(hospital=hospital, created_at__range=(start, end))

    counts_by_source = enquiries.values("source").annotate(
        enquiry_count=Count("id"),
        conversion_count=Count("id", filter=Q(stage=Enquiry.Stage.COMPLETED)),
    )

    # patient_id -> (source, first_enquiry_created_at), first occurrence wins.
    first_enquiry_by_patient = {}
    patient_rows = (
        enquiries.filter(patient__isnull=False)
        .order_by("created_at")
        .values("patient_id", "source", "created_at")
    )
    for row in patient_rows:
        first_enquiry_by_patient.setdefault(row["patient_id"], (row["source"], row["created_at"]))

    patients_by_source = {}
    for patient_id, (source, first_created_at) in first_enquiry_by_patient.items():
        patients_by_source.setdefault(source, {})[patient_id] = first_created_at

    billed_by_source = {}
    for source, patients in patients_by_source.items():
        bucket_q = Q()
        for patient_id, first_created_at in patients.items():
            bucket_q |= Q(patient_id=patient_id, bill_date__gte=first_created_at.date())
        total = HISBillingRecord.objects.filter(hospital=hospital).filter(bucket_q).aggregate(total=Sum("total_amount"))["total"]
        billed_by_source[source] = total or Decimal("0")

    rows = [
        {
            "source": row["source"],
            "enquiry_count": row["enquiry_count"],
            "conversion_count": row["conversion_count"],
            "billed_amount": billed_by_source.get(row["source"], Decimal("0")),
        }
        for row in counts_by_source
    ]

    return {
        "rows": rows,
        "note": "billed_amount is attributed via patient, not a direct enquiry-bill link — treat it as an approximation of source ROI, not a reconciled revenue figure.",
    }


def doctor_revenue(hospital, start, end):
    """Doctor-wise revenue for the window — same patient+bill-date
    approximation as revenue_by_source (there is no direct
    appointment↔bill link from the HIS feed): a completed appointment
    credits its doctor with every HIS bill dated on/after that
    appointment's completion, up to the patient's *next* completed
    appointment with a different doctor (so a bill isn't double-counted
    across doctors when a patient sees more than one)."""
    appointments = (
        Appointment.objects.filter(
            hospital=hospital, status=Appointment.Status.COMPLETED, completed_at__range=(start, end)
        )
        .select_related("doctor")
        .order_by("patient_id", "completed_at")
    )

    # patient_id -> ordered list of (doctor_id, doctor_name, completed_at)
    by_patient = {}
    for appt in appointments:
        by_patient.setdefault(appt.patient_id, []).append((appt.doctor_id, appt.doctor.name, appt.completed_at))

    revenue_by_doctor = {}
    volume_by_doctor = {}
    for patient_id, visits in by_patient.items():
        bills = list(
            HISBillingRecord.objects.filter(hospital=hospital, patient_id=patient_id).order_by("bill_date").values("bill_date", "total_amount")
        )
        for i, (doctor_id, doctor_name, completed_at) in enumerate(visits):
            window_end = visits[i + 1][2].date() if i + 1 < len(visits) else None
            for bill in bills:
                if bill["bill_date"] < completed_at.date():
                    continue
                if window_end is not None and bill["bill_date"] >= window_end:
                    continue
                key = (doctor_id, doctor_name)
                revenue_by_doctor[key] = revenue_by_doctor.get(key, Decimal("0")) + bill["total_amount"]
            volume_by_doctor[(doctor_id, doctor_name)] = volume_by_doctor.get((doctor_id, doctor_name), 0) + 1

    rows = [
        {
            "doctor_id": doctor_id,
            "doctor_name": doctor_name,
            "completed_appointments": volume_by_doctor.get((doctor_id, doctor_name), 0),
            "billed_amount": revenue,
        }
        for (doctor_id, doctor_name), revenue in revenue_by_doctor.items()
    ]
    # Doctors with completed appointments but no matched bills still count as volume.
    for key, count in volume_by_doctor.items():
        if key not in revenue_by_doctor:
            doctor_id, doctor_name = key
            rows.append({"doctor_id": doctor_id, "doctor_name": doctor_name, "completed_appointments": count, "billed_amount": Decimal("0")})

    return {
        "rows": sorted(rows, key=lambda r: r["billed_amount"], reverse=True),
        "note": "billed_amount is attributed via patient + bill-date windows between visits, not a direct appointment-bill link — treat it as an approximation, not a reconciled revenue figure.",
    }


def reminder_delivery_summary(hospital, start, end):
    """§5/§12 (extension) — delivery counts for the automated appointment
    reminder sends, grouped by template purpose (24h / 2h before) and
    channel."""
    messages = Message.objects.filter(
        hospital=hospital,
        template__purpose__in=APPOINTMENT_REMINDER_PURPOSES,
        direction=Message.Direction.OUTBOUND,
        created_at__range=(start, end),
    )
    rows = messages.values("template__purpose", "channel").annotate(count=Count("id"))
    return [{"purpose": row["template__purpose"], "channel": row["channel"], "count": row["count"]} for row in rows]


def render_daily_mis_text(hospital, summary: dict) -> str:
    calls = summary["calls"]
    funnel = summary["enquiry_funnel"]
    return (
        f"Daily MIS — {hospital.name} — {summary['date']}\n"
        f"Calls: {calls['received'] or 0} received, {calls['answered'] or 0} answered, {calls['missed'] or 0} missed\n"
        f"Enquiries: {sum(funnel.values())} new, {funnel.get('visited', 0)} visited\n"
        f"No-shows: {summary['no_show']['no_shows']} ({summary['no_show']['recall_tasks_done']} recalled)\n"
        f"Pending callbacks: {summary['pending_callbacks']}"
    )
