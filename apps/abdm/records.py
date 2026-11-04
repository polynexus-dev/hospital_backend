"""
Which of this hospital's records ABDM can reach, and how each becomes a
FHIR document (apps.integrations.abdm_fhir). A care context is one record,
referenced as "<kind>:<pk>" — the same kinds as GET /abdm-fhir/<kind>/<pk>/.
"""
from dataclasses import dataclass

from django.apps import apps


@dataclass(frozen=True)
class RecordKind:
    kind: str
    model: str  # app_label.Model
    patient_path: str  # lookup from the record to its Patient
    hi_type: str  # ABDM HI type
    builder: str  # function in apps.integrations.abdm_fhir
    label: str
    hospital_path: str = "hospital_id"

    def queryset(self):
        return apps.get_model(self.model).objects.all()


KINDS = {k.kind: k for k in [
    RecordKind("prescription", "patients.Prescription", "patient", "Prescription", "prescription_bundle", "Prescription"),
    RecordKind("lab", "laboratory.LabOrder", "patient", "DiagnosticReport", "lab_report_bundle", "Lab report"),
    RecordKind("imaging", "radiology.RadiologyReport", "radiology_order__patient", "DiagnosticReport", "imaging_report_bundle", "Imaging report"),
    RecordKind("discharge", "ipd.DischargeSummary", "admission__patient", "DischargeSummary", "discharge_summary_bundle", "Discharge summary"),
    RecordKind("opd", "opd.Encounter", "patient", "OPConsultation", "op_consultation_bundle", "OP consultation"),
    RecordKind("immunization", "clinical.Immunization", "patient", "ImmunizationRecord", "immunization_bundle", "Immunization"),
    RecordKind("wellness", "opd.VitalsReading", "encounter__patient", "WellnessRecord", "wellness_bundle", "Vitals", "encounter__hospital_id"),
    RecordKind("document", "patients.Document", "patient", "HealthDocumentRecord", "health_document_bundle", "Health document"),
    RecordKind("invoice", "billing.Bill", "patient", "Invoice", "invoice_bundle", "Invoice"),
]}


def reference(kind, pk):
    return f"{kind}:{pk}"


def parse_reference(ref):
    kind, _, pk = str(ref).partition(":")
    if kind not in KINDS or not pk.isdigit():
        return None, None
    return KINDS[kind], int(pk)


def get_record(hospital_id, kind, pk):
    rk = KINDS[kind]
    return rk.queryset().filter(pk=pk, **{rk.hospital_path: hospital_id}).first()


def build_bundle(rk, record):
    from apps.integrations import abdm_fhir

    return getattr(abdm_fhir, rk.builder)(record)


def care_contexts_for(patient, *, hi_types=None, date_from=None, date_to=None):
    """[(RecordKind, record)] for the patient's records here, oldest first,
    optionally limited to HI types and a creation-date window."""
    out = []
    for rk in KINDS.values():
        if hi_types and rk.hi_type not in hi_types:
            continue
        qs = rk.queryset().filter(**{rk.patient_path: patient, rk.hospital_path: patient.hospital_id})
        if date_from:
            qs = qs.filter(created_at__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__lte=date_to)
        out.extend((rk, r) for r in qs.order_by("created_at"))
    out.sort(key=lambda pair: pair[1].created_at)
    return out


def display(rk, record):
    return f"{rk.label} — {record.created_at:%d %b %Y}"
