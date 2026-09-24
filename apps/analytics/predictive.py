"""
Predictive analytics — deliberately simple, explainable statistics a
hospital manager can sanity-check, not a black box:

* demand forecasts (OPD footfall, admissions): mean of the same weekday
  over the last 8 weeks × a trend factor (last 28 days vs the 28 before),
* bed availability (AAC.5.i): apps.ipd.workflow.predict_bed_availability,
* staffing need (HRM.1.f): forecast occupied beds ÷ target nurse ratio,
  compared with nurses actually rostered,
* stock-out prediction: days of stock left at the 30-day consumption rate,
* appointment no-show risk: patient's own history blended with the
  hospital base rate, weighted up for long booking lead time.
"""
import math
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


def _daily_counts(qs, field, days):
    since = timezone.localdate() - timedelta(days=days)
    rows = qs.filter(**{f"{field}__date__gte": since}).annotate(d=TruncDate(field)).values("d").annotate(n=Count("id"))
    return {r["d"]: r["n"] for r in rows}


def forecast_series(daily, horizon=14):
    today = timezone.localdate()
    by_weekday = defaultdict(list)
    for i in range(1, 57):
        d = today - timedelta(days=i)
        by_weekday[d.weekday()].append(daily.get(d, 0))
    recent = sum(daily.get(today - timedelta(days=i), 0) for i in range(1, 29))
    prior = sum(daily.get(today - timedelta(days=i), 0) for i in range(29, 57))
    trend = (recent / prior) if prior else 1.0
    trend = max(0.5, min(trend, 2.0))
    out = []
    for i in range(horizon):
        d = today + timedelta(days=i)
        hist = by_weekday[d.weekday()]
        base = sum(hist) / len(hist) if hist else 0
        out.append({"date": d, "forecast": round(base * trend, 1), "weekday_history": hist})
    return {"trend_factor": round(trend, 2), "history_days": 56, "forecast": out}


class OPDFootfallForecastView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.appointments.models import Appointment

        qs = Appointment.objects.filter(hospital_id=request.user.hospital_id).exclude(status__in=["cancelled", "rescheduled"])
        return Response(forecast_series(_daily_counts(qs, "created_at", 60), int(request.query_params.get("days", 14))))


class AdmissionsForecastView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.ipd.models import Admission

        qs = Admission.objects.filter(hospital_id=request.user.hospital_id)
        return Response(forecast_series(_daily_counts(qs, "admitted_at", 60), int(request.query_params.get("days", 14))))


class BedForecastView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.ipd.workflow import predict_bed_availability

        return Response(predict_bed_availability(request.user.hospital_id))


class StaffingForecastView(APIView):
    """HRM.1.f — nurses needed per shift for the next 7 days."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.facilities.models import Bed
        from apps.hr.models import Shift
        from apps.ipd.models import Admission
        from apps.ipd.workflow import predict_bed_availability

        h = request.user.hospital_id
        ward_ratio = float(request.query_params.get("ward_ratio", 6))
        icu_ratio = float(request.query_params.get("icu_ratio", 2))
        occupied = Bed.objects.filter(hospital_id=h, status="occupied")
        icu_occ = occupied.filter(bed_type__in=["icu", "ventilator"]).count()
        ward_occ = occupied.count() - icu_occ
        adm_fc = forecast_series(_daily_counts(Admission.objects.filter(hospital_id=h), "admitted_at", 60), 7)["forecast"]
        beds = predict_bed_availability(h)
        daily_discharges = beds["expected_free"].get("24h", 0)
        rows = []
        for day in adm_fc:
            ward_occ = max(0, ward_occ + day["forecast"] - daily_discharges)
            need = math.ceil(ward_occ / ward_ratio) + math.ceil(icu_occ / icu_ratio)
            rostered = Shift.objects.filter(hospital_id=h, shift_date=day["date"], employee__designation__icontains="nurse").values("shift_type").annotate(n=Count("id"))
            by_shift = {r["shift_type"]: r["n"] for r in rostered}
            rows.append({
                "date": day["date"], "forecast_occupied_beds": round(ward_occ + icu_occ, 1), "nurses_needed_per_shift": need,
                "rostered": by_shift, "gaps": {s: need - by_shift.get(s, 0) for s in ("morning", "evening", "night") if by_shift.get(s, 0) < need},
            })
        return Response({"assumptions": {"ward_nurse_ratio": f"1:{ward_ratio:g}", "icu_nurse_ratio": f"1:{icu_ratio:g}"}, "days": rows})


class StockoutForecastView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.pharmacy.models import DispenseRecord, Medicine

        h = request.user.hospital_id
        since = timezone.now() - timedelta(days=30)
        use = dict(DispenseRecord.objects.filter(hospital_id=h, dispensed_at__gte=since).values_list("batch__medicine").annotate(q=Sum("quantity")))
        rows = []
        for m in Medicine.objects.filter(hospital_id=h, is_active=True).annotate(
            stock=Sum("batches__quantity_available", filter=Q(batches__is_quarantined=False, batches__expiry_date__gte=timezone.localdate()))):
            per_day = (use.get(m.pk) or 0) / 30
            if per_day <= 0:
                continue
            days_left = (m.stock or 0) / per_day
            rows.append({"medicine": m.name, "stock": m.stock or 0, "avg_daily_use": round(per_day, 2), "days_of_stock": round(days_left, 1),
                         "stockout_on": timezone.localdate() + timedelta(days=int(days_left)), "is_emergency": m.is_emergency})
        return Response(sorted(rows, key=lambda r: r["days_of_stock"])[:50])


class NoShowRiskView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.appointments.models import Appointment

        h = request.user.hospital_id
        hist = Appointment.objects.filter(hospital_id=h, status__in=["completed", "no_show"])
        base = hist.filter(status="no_show").count() / hist.count() if hist.exists() else 0.1
        per_patient = {r["patient"]: (r["ns"], r["n"]) for r in hist.values("patient").annotate(n=Count("id"), ns=Count("id", filter=Q(status="no_show")))}
        upcoming = Appointment.objects.filter(hospital_id=h, status__in=["booked", "confirmed"], slot__date__gte=timezone.localdate(),
                                              slot__date__lte=timezone.localdate() + timedelta(days=int(request.query_params.get("days", 7)))).select_related("patient", "doctor", "slot")
        rows = []
        for a in upcoming:
            ns, n = per_patient.get(a.patient_id, (0, 0))
            prior_weight = 3
            risk = (ns + base * prior_weight) / (n + prior_weight)
            lead = (a.slot.date - a.created_at.date()).days
            if lead > 14:
                risk *= 1.3
            if a.status == "confirmed":
                risk *= 0.6
            rows.append({"appointment": a.pk, "patient": a.patient.full_name, "doctor": a.doctor.name, "date": a.slot.date, "time": a.slot.start_time,
                         "risk": round(min(risk, 0.95), 2), "history": f"{ns}/{n} missed", "lead_days": lead})
        return Response({"base_rate": round(base, 3), "appointments": sorted(rows, key=lambda r: -r["risk"])})
