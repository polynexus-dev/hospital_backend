"""OPD consultation time — how long each doctor actually spends per patient.

Consultation time = completed_at − consult_started_at (doctor presses "Start
consult" … "Complete"). Waiting time = consult_started_at − checked_in_at
(NABH KPI K22). Both depend on staff pressing the buttons at the right moment,
so durations outside (0, MAX_PLAUSIBLE_MINUTES] are treated as data-entry
artefacts — excluded from the figures and counted separately so the report
shows how clean the data is.
"""
from collections import defaultdict
from datetime import timedelta
from statistics import median

from django.utils import timezone

from .models import Appointment

MAX_PLAUSIBLE_MINUTES = 120
DEFAULT_SHORT_UNDER_MINUTES = 3
# Queue-board estimate: median of this many recent consultations, used once
# a doctor has at least MIN_SAMPLES of them in the look-back window.
RECENT_SAMPLE = 50
MIN_SAMPLES = 10
RECENT_DAYS = 30


def _minutes(start, end):
    return (end - start).total_seconds() / 60


def _completed(qs):
    return qs.filter(consult_started_at__isnull=False, completed_at__isnull=False)


def recent_consult_minutes(doctor) -> tuple[float, bool]:
    """(minutes per patient, is_measured). Median of the doctor's recent real
    consultations; the configured default until there's enough history."""
    since = timezone.now() - timedelta(days=RECENT_DAYS)
    rows = (
        _completed(Appointment.objects.filter(doctor=doctor, completed_at__gte=since))
        .order_by("-completed_at")
        .values_list("consult_started_at", "completed_at")[:RECENT_SAMPLE]
    )
    mins = [m for m in (_minutes(s, e) for s, e in rows) if 0 < m <= MAX_PLAUSIBLE_MINUTES]
    if len(mins) >= MIN_SAMPLES:
        return round(median(mins), 1), True
    return float(doctor.default_consultation_minutes or 15), False


def estimated_wait_minutes(doctor, waiting_count: int, now_serving=None) -> int:
    """Patients ahead × typical consult time, plus whatever is left of the
    consultation in progress (at least a minute if someone is inside)."""
    per_patient, _ = recent_consult_minutes(doctor)
    remaining = 0.0
    if now_serving is not None:
        elapsed = _minutes(now_serving.consult_started_at, timezone.now()) if now_serving.consult_started_at else 0
        remaining = max(per_patient - elapsed, 1)
    return round(waiting_count * per_patient + remaining)


def consultation_report(hospital_id, start, end, *, department=None, doctor=None, short_under=DEFAULT_SHORT_UNDER_MINUTES):
    qs = _completed(Appointment.objects.filter(hospital_id=hospital_id, consult_started_at__date__range=(start, end)))
    if department:
        qs = qs.filter(doctor__department_id=department)
    if doctor:
        qs = qs.filter(doctor_id=doctor)
    rows = qs.select_related("doctor__department", "patient").order_by("consult_started_at")

    per_doc = defaultdict(lambda: {"durations": [], "waits": [], "excluded": 0, "days": defaultdict(list)})
    short = []
    for a in rows:
        d = per_doc[a.doctor_id]
        d["doctor"] = a.doctor
        mins = _minutes(a.consult_started_at, a.completed_at)
        if not 0 < mins <= MAX_PLAUSIBLE_MINUTES:
            d["excluded"] += 1
            continue
        d["durations"].append(mins)
        d["days"][timezone.localdate(a.consult_started_at)].append((a.consult_started_at, a.completed_at))
        if a.checked_in_at and a.consult_started_at >= a.checked_in_at:
            d["waits"].append(_minutes(a.checked_in_at, a.consult_started_at))
        if mins < short_under:
            short.append({
                "appointment": a.pk, "doctor": a.doctor.name, "patient": a.patient.full_name, "uhid": a.patient.uhid,
                "started_at": a.consult_started_at, "minutes": round(mins, 1),
            })

    doctors = []
    for d in per_doc.values():
        doc, mins = d["doctor"], d["durations"]
        if not mins:
            doctors.append({"doctor_id": doc.pk, "doctor": doc.name, "department": doc.department.name if doc.department else "",
                            "consultations": 0, "excluded": d["excluded"]})
            continue
        # Patients per hour over the doctor's actual OPD session each day
        # (first call-in → last completion), so gaps between patients count.
        session_hours = sum(_minutes(min(s for s, _ in v), max(e for _, e in v)) / 60 for v in d["days"].values())
        n_short = sum(1 for m in mins if m < short_under)
        doctors.append({
            "doctor_id": doc.pk,
            "doctor": doc.name,
            "department": doc.department.name if doc.department else "",
            "consultations": len(mins),
            "average_minutes": round(sum(mins) / len(mins), 1),
            "median_minutes": round(median(mins), 1),
            "shortest_minutes": round(min(mins), 1),
            "longest_minutes": round(max(mins), 1),
            "patients_per_hour": round(len(mins) / session_hours, 1) if session_hours >= 0.25 else None,
            "opd_days": len(d["days"]),
            "average_wait_minutes": round(sum(d["waits"]) / len(d["waits"]), 1) if d["waits"] else None,
            "short_consultations": n_short,
            "short_percent": round(100 * n_short / len(mins), 1),
            "configured_minutes": doc.default_consultation_minutes,
            "excluded": d["excluded"],
        })
    doctors.sort(key=lambda r: (r["department"], r["doctor"]))
    all_mins = [m for d in per_doc.values() for m in d["durations"]]
    return {
        "start": start,
        "end": end,
        "short_under_minutes": short_under,
        "max_plausible_minutes": MAX_PLAUSIBLE_MINUTES,
        "overall": {
            "consultations": len(all_mins),
            "average_minutes": round(sum(all_mins) / len(all_mins), 1) if all_mins else None,
            "median_minutes": round(median(all_mins), 1) if all_mins else None,
            "short_consultations": len(short),
            "excluded": sum(d["excluded"] for d in per_doc.values()),
        },
        "doctors": doctors,
        "short_consultations": short[-200:],
    }
