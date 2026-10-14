"""MRD MIS reports (MedNet M2.22): discharges not yet ICD-coded, death
cases (IPD and emergency) and medico-legal cases, with a summary.

GET /mrd/reports/?kind=summary|uncoded|deaths|mlc&start&end&output=csv|xlsx
"""
import csv
import io
from datetime import date, timedelta

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

DONE_STATUSES = ("discharged", "dama", "deceased")


def _d(v, name, default):
    if not v:
        return default
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise serializers.ValidationError({name: "YYYY-MM-DD"})


def _age(dob, on):
    if not dob:
        return ""
    return on.year - dob.year - ((on.month, on.day) < (dob.month, dob.day))


def uncoded(hospital_id, start, end):
    """Closed admissions without ICD coding, or coded but not verified."""
    from apps.ipd.models import Admission

    today = timezone.localdate()
    rows = []
    qs = (Admission.objects.filter(hospital_id=hospital_id, status__in=DONE_STATUSES, discharged_at__date__range=(start, end))
          .select_related("patient", "admitting_doctor", "department", "coding").order_by("discharged_at"))
    for a in qs:
        coding = getattr(a, "coding", None)
        if coding is not None and coding.verified_at:
            continue
        rows.append({
            "admission": a.pk, "uhid": a.patient.uhid, "patient": a.patient.full_name, "admitted_at": a.admitted_at, "discharged_at": a.discharged_at,
            "outcome": a.get_status_display(), "doctor": a.admitting_doctor.name if a.admitting_doctor_id else "",
            "department": a.department.name if a.department_id else "", "state": "Coded, not verified" if coding else "Not coded",
            "days_since_discharge": (today - timezone.localdate(a.discharged_at)).days,
        })
    return rows


def deaths(hospital_id, start, end):
    from apps.emergency.models import EDVisit
    from apps.ipd.models import Admission

    rows = []
    for a in (Admission.objects.filter(hospital_id=hospital_id, status="deceased", discharged_at__date__range=(start, end))
              .select_related("patient", "admitting_doctor", "department", "coding__cause_of_death")):
        coding = getattr(a, "coding", None)
        cause = coding.cause_of_death if coding and coding.cause_of_death_id else None
        record = getattr(a, "record_file", None)
        rows.append({
            "source": "IPD", "ref": a.pk, "uhid": a.patient.uhid, "patient": a.patient.full_name, "age": _age(a.patient.date_of_birth, timezone.localdate(a.discharged_at)),
            "gender": a.patient.gender, "arrived_at": a.admitted_at, "died_at": a.discharged_at,
            "hours_after_arrival": round((a.discharged_at - a.admitted_at).total_seconds() / 3600, 1),
            "doctor": a.admitting_doctor.name if a.admitting_doctor_id else "", "department": a.department.name if a.department_id else "",
            "cause_of_death": f"{cause.code} {cause.title}" if cause else "Not coded", "mlc": bool(record and record.is_mlc),
        })
    for v in (EDVisit.objects.filter(hospital_id=hospital_id, status="deceased")
              .filter(disposition_at__date__range=(start, end)).select_related("patient")):
        rows.append({
            "source": "Emergency", "ref": v.pk, "uhid": v.patient.uhid, "patient": v.patient.full_name, "age": _age(v.patient.date_of_birth, timezone.localdate(v.disposition_at)),
            "gender": v.patient.gender, "arrived_at": v.arrived_at, "died_at": v.disposition_at,
            "hours_after_arrival": round((v.disposition_at - v.arrived_at).total_seconds() / 3600, 1),
            "doctor": "", "department": "Emergency", "cause_of_death": "", "mlc": v.is_mlc,
        })
    rows.sort(key=lambda r: r["died_at"])
    return rows


def mlc(hospital_id, start, end):
    from apps.emergency.models import EDVisit
    from apps.mrd.models import MedicalRecordFile

    rows = []
    items = EDVisit.MLC_CHECKLIST_ITEMS
    for v in (EDVisit.objects.filter(hospital_id=hospital_id, is_mlc=True, arrived_at__date__range=(start, end)).select_related("patient")):
        done = sum(1 for k in items if (v.mlc_checklist or {}).get(k))
        rows.append({
            "source": "Emergency", "mlc_number": v.mlc_number, "type": v.mlc_type, "uhid": v.patient.uhid, "patient": v.patient.full_name,
            "arrived_at": v.arrived_at, "police_station": v.police_station, "police_intimated_at": v.police_intimated_at,
            "intimation_hours": round((v.police_intimated_at - v.arrived_at).total_seconds() / 3600, 1) if v.police_intimated_at else None,
            "checklist": f"{done}/{len(items)}", "outcome": v.get_status_display(),
        })
    for f in (MedicalRecordFile.objects.filter(hospital_id=hospital_id, is_mlc=True, admission__admitted_at__date__range=(start, end))
              .select_related("patient", "admission")):
        rows.append({
            "source": "IPD", "mlc_number": f.file_number, "type": "", "uhid": f.patient.uhid, "patient": f.patient.full_name,
            "arrived_at": f.admission.admitted_at, "police_station": "", "police_intimated_at": None, "intimation_hours": None,
            "checklist": "", "outcome": f.admission.get_status_display(),
        })
    rows.sort(key=lambda r: r["arrived_at"])
    return rows


KINDS = {"uncoded": uncoded, "deaths": deaths, "mlc": mlc}
TITLES = {"uncoded": "Discharges without ICD coding", "deaths": "Death cases", "mlc": "Medico-legal cases"}


def _cell(v):
    if hasattr(v, "isoformat"):
        return timezone.localtime(v).strftime("%Y-%m-%d %H:%M") if hasattr(v, "tzinfo") and getattr(v, "tzinfo", None) else v.isoformat()
    if v is True:
        return "Yes"
    if v is False:
        return "No"
    return "" if v is None else v


class MRDReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.has_perm("mrd.view_medicalrecordfile"):
            return Response({"detail": "You don't have access to MRD reports."}, status=403)
        q = request.query_params
        today = timezone.localdate()
        start, end = _d(q.get("start"), "start", today - timedelta(days=30)), _d(q.get("end"), "end", today)
        kind = q.get("kind", "summary")
        hid = request.user.hospital_id
        if kind == "summary":
            data = {k: fn(hid, start, end) for k, fn in KINDS.items()}
            return Response({"start": start, "end": end, "uncoded": len(data["uncoded"]),
                             "not_coded": sum(1 for r in data["uncoded"] if r["state"] == "Not coded"),
                             "overdue_coding": sum(1 for r in data["uncoded"] if r["days_since_discharge"] > 7),
                             "deaths": len(data["deaths"]), "ipd_deaths": sum(1 for r in data["deaths"] if r["source"] == "IPD"),
                             "deaths_within_48h": sum(1 for r in data["deaths"] if r["hours_after_arrival"] < 48),
                             "mlc": len(data["mlc"]), "mlc_police_not_intimated": sum(1 for r in data["mlc"] if r["source"] == "Emergency" and not r["police_intimated_at"])})
        if kind not in KINDS:
            raise serializers.ValidationError({"kind": "summary | uncoded | deaths | mlc"})
        rows = KINDS[kind](hid, start, end)
        if q.get("output") in ("csv", "xlsx"):
            cols = list(rows[0].keys()) if rows else ["no_records"]
            table = [[TITLES[kind], f"{start} to {end}"], [c.replace("_", " ").capitalize() for c in cols]] + [[_cell(r[c]) for c in cols] for r in rows]
            name = f"mrd-{kind}-{start}-to-{end}"
            if q["output"] == "xlsx":
                from apps.core.xlsx import build_xlsx

                resp = HttpResponse(build_xlsx(table, kind), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                resp["Content-Disposition"] = f'attachment; filename="{name}.xlsx"'
                return resp
            buf = io.StringIO()
            csv.writer(buf).writerows(table)
            resp = HttpResponse(buf.getvalue(), content_type="text/csv; charset=utf-8")
            resp["Content-Disposition"] = f'attachment; filename="{name}.csv"'
            return resp
        return Response({"kind": kind, "title": TITLES[kind], "start": start, "end": end, "count": len(rows), "rows": rows})
