"""
NABH KPI engine (HIS/EMR IMS.2.a/b/c).

`NABH_KPIS` — the 32 hospital-accreditation KPIs (PSQ.3a–3d, NABH Hospital
Standards) listed in Annexure 8 of the HIS/EMR standard.
`DHS_KPIS` — the Digital Health Standard KPIs (Annexure 9).

Each computable KPI is a function (hospital_id, start, end) returning
(numerator, denominator). Values are derived with the formula/multiplier
in the definition. Where NABH itself says an indicator is audit-based
("provision for entering the manual/electronically collected data"), the
KPI is `manual=True` and its value comes from quality.KPIManualEntry; a
computable KPI with a manual entry for the same period uses the manual
figure (the auditor's number wins over the system's).
"""
import csv
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Callable, Optional
from xml.etree import ElementTree as ET

from django.db.models import Count, F, Q
from django.utils import timezone


def _dt(d, end=False):
    return timezone.make_aware(datetime.combine(d, time.max if end else time.min))


def parse_period(request, default_days=30):
    today = timezone.localdate()
    try:
        start = date.fromisoformat(request.query_params.get("start")) if request.query_params.get("start") else today - timedelta(days=default_days)
        end = date.fromisoformat(request.query_params.get("end")) if request.query_params.get("end") else today
    except ValueError:
        start, end = today - timedelta(days=default_days), today
    return start, end


def quarter_bounds(d):
    q = (d.month - 1) // 3
    start = date(d.year, q * 3 + 1, 1)
    end = (date(d.year + (q == 3), (q * 3 + 3) % 12 + 1, 1) - timedelta(days=1))
    return start, end


def previous_quarter(today=None):
    today = today or timezone.localdate()
    start, _ = quarter_bounds(today)
    return quarter_bounds(start - timedelta(days=1))


@dataclass
class KPI:
    code: str
    name: str
    unit: str
    multiplier: int = 100
    fn: Optional[Callable] = None
    manual: bool = False
    standard: str = "PSQ.3a"
    kind: str = "nabh"  # nabh | dhs


# --- shared denominators ----------------------------------------------------


def patient_days(hospital_id, start, end):
    from apps.ipd.models import Admission

    s, e = _dt(start), _dt(end, True)
    total = 0.0
    for a in Admission.objects.filter(hospital_id=hospital_id, admitted_at__lte=e).filter(Q(discharged_at__isnull=True) | Q(discharged_at__gte=s)).only("admitted_at", "discharged_at"):
        a_s = max(a.admitted_at, s)
        a_e = min(a.discharged_at or timezone.now(), e)
        if a_e > a_s:
            total += (a_e - a_s).total_seconds() / 86400
    return round(total, 2)


def device_days(hospital_id, device, start, end):
    from apps.infection_control.models import DeviceEpisode

    s, e = _dt(start), _dt(end, True)
    return sum(
        d.device_days_between(s, e)
        for d in DeviceEpisode.objects.filter(hospital_id=hospital_id, device=device, inserted_at__lte=e).filter(Q(removed_at__isnull=True) | Q(removed_at__gte=s))
    )


def _admissions(hospital_id, start, end):
    from apps.ipd.models import Admission

    return Admission.objects.filter(hospital_id=hospital_id, admitted_at__range=(_dt(start), _dt(end, True)))


def _discharges(hospital_id, start, end):
    from apps.ipd.models import Admission

    return Admission.objects.filter(hospital_id=hospital_id, discharged_at__range=(_dt(start), _dt(end, True)))


def _incidents(hospital_id, start, end):
    from .models import SafetyIncident

    return SafetyIncident.objects.filter(hospital_id=hospital_id, occurred_at__range=(_dt(start), _dt(end, True)))


def _avg_minutes(pairs):
    mins = [(b - a).total_seconds() / 60 for a, b in pairs if a and b and b >= a]
    return (round(sum(mins), 2), len(mins))


# --- NABH hospital KPIs -----------------------------------------------------


def k01_initial_assessment_time(h, s, e):
    from apps.clinical.models import ClinicalAssessment

    pairs = []
    for adm in _admissions(h, s, e).exclude(admission_type="daycare"):
        first = ClinicalAssessment.objects.filter(admission=adm, kind="initial").order_by("assessed_at").values_list("assessed_at", flat=True).first()
        if first:
            pairs.append((adm.admitted_at, first))
    return _avg_minutes(pairs)


def k02_reporting_errors(h, s, e):
    from django.contrib.contenttypes.models import ContentType

    from apps.core.models import Amendment
    from apps.laboratory.models import LabResult
    from apps.radiology.models import RadiologyReport

    cts = [ContentType.objects.get_for_model(LabResult), ContentType.objects.get_for_model(RadiologyReport)]
    num = Amendment.objects.filter(hospital_id=h, content_type__in=cts, amended_at__range=(_dt(s), _dt(e, True))).count()
    den = LabResult.objects.filter(hospital_id=h, created_at__range=(_dt(s), _dt(e, True))).count() + RadiologyReport.objects.filter(hospital_id=h, created_at__range=(_dt(s), _dt(e, True))).count()
    return num, den


def k04_medication_errors(h, s, e):
    from apps.nursing.models import MedicationAdministration

    from .models import MedicationError

    num = MedicationError.objects.filter(hospital_id=h, occurred_at__range=(_dt(s), _dt(e, True))).count()
    den = MedicationAdministration.objects.filter(hospital_id=h, administered_at__range=(_dt(s), _dt(e, True))).count()
    return num, den


def k05_adr(h, s, e):
    num = _incidents(h, s, e).filter(incident_type="adr").values("patient").distinct().count()
    return num, _admissions(h, s, e).count()


def k06_unplanned_return_ot(h, s, e):
    from apps.ot.models import OperativeNote

    notes = OperativeNote.objects.filter(hospital_id=h, started_at__range=(_dt(s), _dt(e, True)))
    return notes.filter(ot_schedule__surgery_request__is_unplanned_return=True).count(), notes.count()


def k07_rescheduling_surgeries(h, s, e):
    from apps.ot.models import OTSchedule

    qs = OTSchedule.objects.filter(hospital_id=h, scheduled_start__range=(_dt(s), _dt(e, True)))
    return qs.filter(reschedule_count__gt=0).count(), qs.count()


def k08_transfusion_reactions(h, s, e):
    from apps.bloodbank.models import Transfusion

    qs = Transfusion.objects.filter(hospital_id=h, transfused_at__range=(_dt(s), _dt(e, True)))
    return qs.filter(had_reaction=True).count(), qs.count()


def k09_icu_smr(h, s, e):
    from apps.icu.models import ICUAdmission

    qs = ICUAdmission.objects.filter(hospital_id=h, discharged_at__range=(_dt(s), _dt(e, True)), predicted_mortality__isnull=False)
    actual = qs.filter(outcome="death").count()
    predicted = sum(float(x) for x in qs.values_list("predicted_mortality", flat=True)) / 100
    return actual, round(predicted, 4)


def k10_ed_return_72h(h, s, e):
    from apps.emergency.models import EDVisit

    visits = list(EDVisit.objects.filter(hospital_id=h, arrived_at__range=(_dt(s), _dt(e, True))).order_by("patient_id", "arrived_at").values("patient_id", "arrived_at"))
    returns = 0
    for prev, cur in zip(visits, visits[1:]):
        if prev["patient_id"] == cur["patient_id"] and cur["arrived_at"] - prev["arrived_at"] <= timedelta(hours=72):
            returns += 1
    return returns, len(visits)


def k11_pressure_ulcers(h, s, e):
    return _incidents(h, s, e).filter(incident_type="pressure_ulcer").count(), patient_days(h, s, e)


def _hai(kind, device):
    def fn(h, s, e):
        from apps.infection_control.models import HAIIncident

        num = HAIIncident.objects.filter(hospital_id=h, infection_type=kind, status__in=["confirmed", "resolved"], onset_date__range=(s, e)).count()
        return num, device_days(h, device, s, e)
    return fn


def k15_ssi(h, s, e):
    from apps.infection_control.models import HAIIncident
    from apps.ot.models import OperativeNote

    num = HAIIncident.objects.filter(hospital_id=h, infection_type="ssi", status__in=["confirmed", "resolved"], onset_date__range=(s, e)).count()
    return num, OperativeNote.objects.filter(hospital_id=h, started_at__range=(_dt(s), _dt(e, True))).count()


def k16_hand_hygiene(h, s, e):
    from django.db.models import Sum

    from apps.infection_control.models import HandHygieneAudit

    agg = HandHygieneAudit.objects.filter(hospital_id=h, audit_date__range=(s, e)).aggregate(c=Sum("compliant"), o=Sum("opportunities"))
    return agg["c"] or 0, agg["o"] or 0


def k17_prophylactic_antibiotic(h, s, e):
    from apps.ot.models import SurgicalSafetyChecklist

    qs = SurgicalSafetyChecklist.objects.filter(hospital_id=h, created_at__range=(_dt(s), _dt(e, True)), antibiotic_prophylaxis_indicated=True)
    return qs.filter(antibiotic_given_within_60_min=True).count(), qs.count()


def k19_surgery_cancellation(h, s, e):
    from apps.ot.models import OTSchedule

    qs = OTSchedule.objects.filter(hospital_id=h, scheduled_start__range=(_dt(s), _dt(e, True)))
    return qs.filter(cancelled_at__isnull=False).count(), qs.count()


def k20_blood_tat(h, s, e):
    from apps.bloodbank.models import CrossMatchRequest

    pairs = CrossMatchRequest.objects.filter(hospital_id=h, created_at__range=(_dt(s), _dt(e, True)), issued_at__isnull=False).values_list("created_at", "issued_at")
    return _avg_minutes(pairs)


def k22_opd_waiting(h, s, e):
    from apps.appointments.models import Appointment

    pairs = Appointment.objects.filter(hospital_id=h, checked_in_at__range=(_dt(s), _dt(e, True)), consult_started_at__isnull=False).values_list("checked_in_at", "consult_started_at")
    return _avg_minutes(pairs)


def k23_diagnostic_waiting(h, s, e):
    from apps.laboratory.models import SampleCollection

    pairs = SampleCollection.objects.filter(hospital_id=h, collected_at__range=(_dt(s), _dt(e, True))).values_list("lab_order__ordered_at", "collected_at")
    return _avg_minutes(pairs)


def k24_discharge_time(h, s, e):
    pairs = _discharges(h, s, e).filter(discharge_initiated_at__isnull=False).values_list("discharge_initiated_at", "discharged_at")
    return _avg_minutes(pairs)


def k25_incomplete_consent(h, s, e):
    from apps.clinical.models import ConsentRecord

    qs = ConsentRecord.objects.filter(hospital_id=h, obtained_at__range=(_dt(s), _dt(e, True)))
    incomplete = qs.filter(Q(witness_name="") | (Q(signature_image="") & Q(signed_document="")) | (Q(consent_type__in=["procedure", "anaesthesia", "high_risk", "blood"]) & Q(risks_explained=""))).count()
    return incomplete, qs.count()


def k26_emergency_stockouts(h, s, e):
    from apps.pharmacy.models import StockOutEvent

    return StockOutEvent.objects.filter(hospital_id=h, is_emergency_medication=True, occurred_at__range=(_dt(s), _dt(e, True))).count(), 1


def k27_mock_drill_variations(h, s, e):
    from django.db.models import Sum

    from .models import MockDrill

    return MockDrill.objects.filter(hospital_id=h, drill_date__range=(s, e)).aggregate(v=Sum("variations_observed"))["v"] or 0, 1


def k28_falls(h, s, e):
    return _incidents(h, s, e).filter(incident_type="fall").count(), patient_days(h, s, e)


def k29_near_misses(h, s, e):
    qs = _incidents(h, s, e)
    return qs.filter(harm="near_miss").count(), qs.count()


def k30_needlestick(h, s, e):
    from apps.infection_control.models import StaffExposure

    return StaffExposure.objects.filter(hospital_id=h, exposure_type="needlestick", occurred_at__range=(_dt(s), _dt(e, True))).count(), patient_days(h, s, e)


def k31_handovers(h, s, e):
    from apps.clinical.models import ShiftHandover

    qs = ShiftHandover.objects.filter(hospital_id=h, handed_over_at__range=(_dt(s), _dt(e, True)))
    return qs.filter(acknowledged_at__isnull=False).exclude(situation="").count(), qs.count()


NABH_KPIS = [
    KPI("K01", "Time for initial assessment of indoor patients", "minutes", 1, k01_initial_assessment_time),
    KPI("K02", "Reporting errors per 1000 investigations", "per 1000 tests", 1000, k02_reporting_errors),
    KPI("K03", "Adherence to safety precautions by diagnostics staff", "%", 100, manual=True),
    KPI("K04", "Medication errors rate", "%", 100, k04_medication_errors),
    KPI("K05", "Patients developing adverse drug reactions", "%", 100, k05_adr),
    KPI("K06", "Unplanned return to OT", "%", 100, k06_unplanned_return_ot),
    KPI("K07", "Re-scheduling of surgeries", "%", 100, k07_rescheduling_surgeries),
    KPI("K08", "Transfusion reactions", "%", 100, k08_transfusion_reactions),
    KPI("K09", "Standardised mortality ratio (ICU)", "ratio", 1, k09_icu_smr),
    KPI("K10", "Return to emergency within 72 hours", "%", 100, k10_ed_return_72h),
    KPI("K11", "Hospital-associated pressure ulcers", "per 1000 patient-days", 1000, k11_pressure_ulcers, standard="PSQ.3b"),
    KPI("K12", "Catheter-associated UTI (CAUTI) rate", "per 1000 catheter-days", 1000, _hai("cauti", "urinary_catheter"), standard="PSQ.3b"),
    KPI("K13", "Ventilator-associated pneumonia (VAP) rate", "per 1000 ventilator-days", 1000, _hai("vap", "ventilator"), standard="PSQ.3b"),
    KPI("K14", "Central line bloodstream infection (CLABSI) rate", "per 1000 line-days", 1000, _hai("clabsi", "central_line"), standard="PSQ.3b"),
    KPI("K15", "Surgical site infection rate", "%", 100, k15_ssi, standard="PSQ.3b"),
    KPI("K16", "Hand hygiene compliance", "%", 100, k16_hand_hygiene, standard="PSQ.3b"),
    KPI("K17", "Prophylactic antibiotic within the specified timeframe", "%", 100, k17_prophylactic_antibiotic, standard="PSQ.3b"),
    KPI("K18", "Appropriate antibiotic prophylaxis (audit)", "%", 100, manual=True, standard="PSQ.3b"),
    KPI("K19", "Cancellation of surgeries", "%", 100, k19_surgery_cancellation, standard="PSQ.3c"),
    KPI("K20", "Turnaround time for issue of blood", "minutes", 1, k20_blood_tat, standard="PSQ.3c"),
    KPI("K21", "Nurse–patient ratio (ICU / wards)", "ratio", 1, manual=True, standard="PSQ.3c"),
    KPI("K22", "Waiting time for OPD consultation", "minutes", 1, k22_opd_waiting, standard="PSQ.3c"),
    KPI("K23", "Waiting time for diagnostics", "minutes", 1, k23_diagnostic_waiting, standard="PSQ.3c"),
    KPI("K24", "Time taken for discharge", "minutes", 1, k24_discharge_time, standard="PSQ.3c"),
    KPI("K25", "Medical records with incomplete/improper consent", "%", 100, k25_incomplete_consent, standard="PSQ.3c"),
    KPI("K26", "Stock-outs of emergency medications", "count", 1, k26_emergency_stockouts, standard="PSQ.3c"),
    KPI("K27", "Variations observed in mock drills", "count", 1, k27_mock_drill_variations, standard="PSQ.3d"),
    KPI("K28", "Patient fall rate", "per 1000 patient-days", 1000, k28_falls, standard="PSQ.3d"),
    KPI("K29", "Near misses", "% of incidents", 100, k29_near_misses, standard="PSQ.3d"),
    KPI("K30", "Needlestick injuries", "per 1000 patient-days", 1000, k30_needlestick, standard="PSQ.3d"),
    KPI("K31", "Appropriate handovers during shift change", "%", 100, k31_handovers, standard="PSQ.3d"),
    KPI("K32", "Compliance to prescription in capitals (audit)", "%", 100, manual=True, standard="PSQ.3d"),
]


# --- NABH Digital Health Standard KPIs ------------------------------------


def d01_digital_appointments(h, s, e):
    from apps.appointments.models import Appointment

    qs = Appointment.objects.filter(hospital_id=h, created_at__range=(_dt(s), _dt(e, True)))
    return qs.exclude(source__in=["walk_in", ""]).count(), qs.count()


def d02_abha_created(h, s, e):
    from apps.abdm.models import AbhaLink

    return AbhaLink.objects.filter(hospital_id=h, created_at__range=(_dt(s), _dt(e, True))).count(), 1


def d03_digital_prescriptions(h, s, e):
    from apps.opd.models import Encounter
    from apps.patients.models import Prescription

    enc = Encounter.objects.filter(hospital_id=h, created_at__range=(_dt(s), _dt(e, True)))
    return Prescription.objects.filter(hospital_id=h, encounter__in=enc).values("encounter").distinct().count(), enc.count()


def d04_digital_nursing_notes(h, s, e):
    from apps.nursing.models import NursingNote

    n = NursingNote.objects.filter(hospital_id=h, created_at__range=(_dt(s), _dt(e, True))).count()
    return n, n  # every nursing note in this system is digital by construction


def d05_ipd_digital_feedback(h, s, e):
    from apps.feedback.models import NPSResponse

    disch = _discharges(h, s, e)
    with_fb = NPSResponse.objects.filter(hospital_id=h, patient__in=disch.values("patient")).values("patient").distinct().count()
    return with_fb, disch.count()


def d06_opd_digital_feedback(h, s, e):
    from apps.appointments.models import Appointment
    from apps.feedback.models import FeedbackRequest

    done = Appointment.objects.filter(hospital_id=h, completed_at__range=(_dt(s), _dt(e, True)))
    fb = FeedbackRequest.objects.filter(hospital_id=h, appointment__in=done, status="responded").values("appointment").distinct().count()
    return fb, done.count()


def d07_digital_ipd_billing(h, s, e):
    from apps.billing.models import Bill

    disch = _discharges(h, s, e)
    return Bill.objects.filter(hospital_id=h, admission__in=disch).values("admission").distinct().count(), disch.count()


def d08_digital_discharge_summary(h, s, e):
    from apps.ipd.models import DischargeSummary

    disch = _discharges(h, s, e)
    return DischargeSummary.objects.filter(admission__in=disch).count(), disch.count()


def d09_uptime(h, s, e):
    from .models import SystemHeartbeat

    beats = SystemHeartbeat.objects.filter(beat_at__range=(_dt(s), _dt(e, True))).count()
    now = timezone.now()
    span_end = min(_dt(e, True), now)
    expected = max(int((span_end - _dt(s)).total_seconds() // (SystemHeartbeat.INTERVAL_SECONDS)), 0)
    return min(beats, expected), expected


def d10_abha_linked_records(h, s, e):
    from apps.abdm.models import AbhaLink
    from apps.patients.models import Patient

    reg = Patient.objects.filter(hospital_id=h, created_at__range=(_dt(s), _dt(e, True)))
    linked = AbhaLink.objects.filter(hospital_id=h, patient__in=reg).values("patient").distinct().count()
    return linked, reg.count()


DHS_KPIS = [
    KPI("D01", "Digital OPD appointments", "%", 100, d01_digital_appointments, standard="AAC.3.a", kind="dhs"),
    KPI("D02", "ABHA accounts created / linked", "count", 1, d02_abha_created, standard="AAC.2.c", kind="dhs"),
    KPI("D03", "Digital OPD prescriptions", "%", 100, d03_digital_prescriptions, standard="COP.3.a", kind="dhs"),
    KPI("D04", "Digital IPD nursing notes", "%", 100, d04_digital_nursing_notes, standard="COP.2.b", kind="dhs"),
    KPI("D05", "IPD digital feedback", "%", 100, d05_ipd_digital_feedback, standard="AAC.8.a", kind="dhs"),
    KPI("D06", "OPD digital feedback", "%", 100, d06_opd_digital_feedback, standard="AAC.8.a", kind="dhs"),
    KPI("D07", "Digital IPD billing", "%", 100, d07_digital_ipd_billing, standard="FPM", kind="dhs"),
    KPI("D08", "Digital discharge summaries", "%", 100, d08_digital_discharge_summary, standard="AAC.6", kind="dhs"),
    KPI("D09", "System uptime", "%", 100, d09_uptime, standard="DOM", kind="dhs"),
    KPI("D10", "New registrations linked to ABHA", "%", 100, d10_abha_linked_records, standard="AAC.1.e", kind="dhs"),
]

ALL_KPIS = {k.code: k for k in NABH_KPIS + DHS_KPIS}
COUNT_UNITS = {"count"}


def compute(hospital_id, start, end, codes=None):
    from .models import KPIManualEntry

    manual = {}
    for m in KPIManualEntry.objects.filter(hospital_id=hospital_id, period_start__gte=start, period_end__lte=end):
        n, d = manual.get(m.kpi_code, (Decimal(0), Decimal(0)))
        manual[m.kpi_code] = (n + m.numerator, d + m.denominator)

    rows = []
    for kpi in (ALL_KPIS[c] for c in codes) if codes else ALL_KPIS.values():
        row = {"code": kpi.code, "name": kpi.name, "unit": kpi.unit, "standard": kpi.standard, "kind": kpi.kind,
               "numerator": None, "denominator": None, "value": None, "source": "unavailable", "note": ""}
        num = den = None
        if kpi.code in manual:
            num, den = manual[kpi.code]
            row["source"] = "manual"
        elif kpi.fn is not None:
            try:
                num, den = kpi.fn(hospital_id, start, end)
                row["source"] = "system"
            except Exception as exc:  # a module not in use / field not captured yet
                row["note"] = f"Not computable: {type(exc).__name__}"
        else:
            row["note"] = "Audit-based indicator — enter the audit figures manually."
        if num is not None:
            row["numerator"], row["denominator"] = float(num), float(den)
            if kpi.unit in COUNT_UNITS:
                row["value"] = float(num)
            elif kpi.unit == "minutes" and row["source"] == "system":
                row["value"] = round(float(num) / float(den), 2) if den else None
            elif den:
                row["value"] = round(float(num) * kpi.multiplier / float(den), 4)
            if row["value"] is None and not row["note"]:
                row["note"] = "No data in period (denominator is zero)."
        rows.append(row)
    return rows


def snapshot(hospital_id, start, end, *, publish=False):
    from .models import KPISnapshot

    out = []
    for row in compute(hospital_id, start, end):
        snap, _ = KPISnapshot.objects.update_or_create(
            hospital_id=hospital_id, kpi_code=row["code"], period_start=start, period_end=end,
            defaults={
                "kpi_name": row["name"], "numerator": row["numerator"], "denominator": row["denominator"],
                "value": row["value"], "unit": row["unit"], "source": row["source"], "note": row["note"][:300],
                "is_published": publish, "published_at": timezone.now() if publish else None,
            },
        )
        out.append(snap)
    return out


# --- exports (IMS.2.b/c: JSON, CSV, XML, XLSX, PDF) --------------------------

FIELDS = ["code", "name", "standard", "unit", "numerator", "denominator", "value", "source", "note"]


def export(rows, fmt, *, hospital_name="", start=None, end=None):
    """Returns (bytes, content_type, extension)."""
    meta = {"hospital": hospital_name, "period_start": str(start), "period_end": str(end), "generated_at": timezone.now().isoformat()}
    if fmt == "json":
        return json.dumps({**meta, "kpis": rows}, indent=2, default=str).encode(), "application/json", "json"
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
        return buf.getvalue().encode("utf-8-sig"), "text/csv", "csv"
    if fmt == "xml":
        root = ET.Element("NABHKPIReport", {k: v for k, v in meta.items()})
        for r in rows:
            el = ET.SubElement(root, "KPI", {"code": r["code"]})
            for f in FIELDS[1:]:
                ET.SubElement(el, f).text = "" if r.get(f) is None else str(r.get(f))
        return ET.tostring(root, encoding="utf-8", xml_declaration=True), "application/xml", "xml"
    if fmt == "xlsx":
        from apps.core.xlsx import build_xlsx

        table = [[f"{hospital_name} — NABH KPIs {start} to {end}"], FIELDS] + [[r.get(f) for f in FIELDS] for r in rows]
        return build_xlsx(table, "NABH KPIs"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    if fmt == "pdf":
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=landscape(A4))
        styles = getSampleStyleSheet()
        data = [["Code", "Indicator", "Unit", "Numerator", "Denominator", "Value", "Source"]]
        for r in rows:
            data.append([r["code"], Paragraph(r["name"], styles["BodyText"]), r["unit"], r["numerator"] if r["numerator"] is not None else "-",
                         r["denominator"] if r["denominator"] is not None else "-", r["value"] if r["value"] is not None else "-", r["source"]])
        t = Table(data, colWidths=[40, 300, 110, 70, 80, 70, 60], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")), ("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 8)]))
        doc.build([Paragraph(f"<b>{hospital_name}</b> — NABH Key Performance Indicators, {start} to {end}", styles["Title"]), t])
        return buf.getvalue(), "application/pdf", "pdf"
    raise ValueError("format must be one of json, csv, xml, xlsx, pdf")


def hai_rates(hospital_id, start, end):
    rows = compute(hospital_id, start, end, codes=["K12", "K13", "K14", "K15", "K16"])
    return {"period": {"start": start, "end": end}, "rates": rows}
