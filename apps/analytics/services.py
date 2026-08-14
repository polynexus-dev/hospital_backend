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


def opd_snapshot(hospital):
    """ERP ops dashboard, OPD metrics only — docs/erp/06-navigation-and-dashboards.md
    §4. First entry in what becomes a fuller ERP ops dashboard as later
    phases (IPD bed occupancy, ICU, OT utilization, lab TAT, pharmacy
    stock) add their own snapshot functions alongside this one — not
    parameterized by date range like the CRM reports above, "today" is
    the only view a live ops dashboard needs."""
    from apps.opd.models import Encounter

    start, end = _today_range()
    today_appointments = Appointment.objects.filter(hospital=hospital, slot__date=timezone.localdate())
    return {
        "encounters_today": Encounter.objects.filter(hospital=hospital, created_at__gte=start, created_at__lt=end).count(),
        "waiting": today_appointments.filter(status=Appointment.Status.CHECKED_IN).count(),
        "in_consult": today_appointments.filter(status=Appointment.Status.IN_CONSULT).count(),
        "completed_today": today_appointments.filter(status=Appointment.Status.COMPLETED, completed_at__gte=start, completed_at__lt=end).count(),
    }


def bed_occupancy_snapshot(hospital):
    """ERP ops dashboard, bed occupancy (docs/erp/06-navigation-and-dashboards.md
    §4, Phase 4 addition) — a separate function from opd_snapshot above,
    not folded into it, since bed occupancy is IPD/facilities data, not
    OPD; both feed the same growing dashboard section without pretending
    to be the same concern."""
    from apps.facilities.models import Bed

    beds = Bed.objects.filter(hospital=hospital)
    total = beds.count()
    occupied = beds.filter(status=Bed.Status.OCCUPIED).count()
    return {
        "total_beds": total,
        "occupied_beds": occupied,
        "occupancy_pct": round(occupied / total * 100, 1) if total else 0,
    }


def icu_occupancy_snapshot(hospital):
    from apps.icu.models import ICUAdmission

    active_icu = ICUAdmission.objects.filter(hospital=hospital, discharged_at__isnull=True)
    total_active = active_icu.count()
    ventilator_active = active_icu.filter(ventilator_required=True).count()
    return {
        "active_icu_admissions": total_active,
        "ventilator_supported": ventilator_active,
    }


def ot_utilization_snapshot(hospital):
    from apps.ot.models import OTSchedule, SurgeryRequest

    start, end = _today_range()
    today_schedules = OTSchedule.objects.filter(hospital=hospital, scheduled_start__gte=start, scheduled_start__lt=end)
    return {
        "scheduled_surgeries_today": today_schedules.count(),
        "pending_surgery_requests": SurgeryRequest.objects.filter(hospital=hospital, status=SurgeryRequest.Status.REQUESTED).count(),
    }


def lab_tat_snapshot(hospital):
    """ERP ops dashboard, lab turnaround time (Phase 5 addition — see
    opd_snapshot's docstring for why these live as separate functions
    rather than one combined dashboard query). TAT is measured order->
    verified-result, the full round trip a clinician actually waits on,
    not order->first-entered-result."""
    from apps.laboratory.models import LabOrder, LabResult

    start, end = _today_range()
    verified_today = LabResult.objects.filter(
        hospital=hospital, finalized_at__gte=start, finalized_at__lt=end, finalized_at__isnull=False,
    ).select_related("lab_order")

    tat_minutes = [(r.finalized_at - r.lab_order.ordered_at).total_seconds() / 60 for r in verified_today]
    avg_tat_minutes = round(sum(tat_minutes) / len(tat_minutes), 1) if tat_minutes else None

    orders = LabOrder.objects.filter(hospital=hospital)
    return {
        "orders_today": orders.filter(ordered_at__gte=start, ordered_at__lt=end).count(),
        "pending_orders": orders.exclude(status=LabOrder.Status.VERIFIED).count(),
        "avg_tat_minutes": avg_tat_minutes,
    }


def pharmacy_low_stock_snapshot(hospital):
    """ERP ops dashboard, pharmacy stock (Phase 5 addition). A medicine
    counts as low stock only when it has a reorder_level set (0 means "not
    tracked for reordering") and its total across all batches has fallen
    to or below it — same rule as apps.pharmacy.serializers.MedicineSerializer
    .total_available, computed here directly for the hospital-wide list
    rather than round-tripping through every Medicine's serializer."""
    from apps.pharmacy.models import Medicine

    medicines = Medicine.objects.filter(hospital=hospital, is_active=True).annotate(total_available=Sum("batches__quantity_available"))
    low_stock = [m for m in medicines if m.reorder_level > 0 and (m.total_available or 0) <= m.reorder_level]
    return {
        "total_medicines": medicines.count(),
        "low_stock_count": len(low_stock),
        "low_stock_medicines": [
            {"id": m.id, "name": m.name, "available": m.total_available or 0, "reorder_level": m.reorder_level}
            for m in sorted(low_stock, key=lambda m: m.total_available or 0)[:10]
        ],
    }


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
