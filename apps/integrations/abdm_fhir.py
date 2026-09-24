"""
ABDM / NRCeS FHIR R4 document bundles (NABH IMS.1.a–c; AAC.3.n, AAC.4.m,
AAC.6.f, COP.1.o — health records linked to ABHA are shared as these
bundles through the ABDM HIE-CM once apps.abdm's gateway is live).

Each builder returns a `Bundle` of type `document` whose first entry is a
`Composition` carrying the NRCeS profile and SNOMED CT document type,
followed by every resource the Composition references — the shape the
ABDM sandbox validator expects for the matching HI type.
"""
import uuid
from datetime import datetime

from django.utils import timezone

NRCES = "https://nrces.in/ndhm/fhir/r4/StructureDefinition"
SNOMED = "http://snomed.info/sct"
LOINC = "http://loinc.org"
ICD10 = "http://hl7.org/fhir/sid/icd-10"

DOC_TYPES = {
    "OPConsultation": ("371530004", "Clinical consultation report"),
    "Prescription": ("440545006", "Prescription record"),
    "DiagnosticReport": ("721981007", "Diagnostic studies report"),
    "DischargeSummary": ("373942005", "Discharge summary"),
}


def _id():
    return str(uuid.uuid4())


def _ref(resource):
    return f"urn:uuid:{resource['id']}"


def _iso(dt):
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return timezone.localtime(dt).isoformat() if timezone.is_aware(dt) else dt.isoformat()
    return dt.isoformat()


def _patient(p):
    abha = getattr(getattr(p, "abha_link", None), "abha_number", "") if hasattr(p, "abha_link") else ""
    identifiers = [{"type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "MR", "display": "Medical record number"}]},
                    "system": "https://ndhm.in/SwasthID", "value": p.uhid or str(p.pk)}]
    if abha:
        identifiers.append({"system": "https://healthid.ndhm.gov.in", "value": abha})
    return {
        "resourceType": "Patient", "id": _id(), "meta": {"profile": [f"{NRCES}/Patient"]}, "identifier": identifiers,
        "name": [{"text": p.full_name}], "gender": {"male": "male", "female": "female"}.get(p.gender, "unknown"),
        **({"birthDate": p.date_of_birth.isoformat()} if p.date_of_birth else {}),
    }


def _practitioner(name, reg=""):
    res = {"resourceType": "Practitioner", "id": _id(), "meta": {"profile": [f"{NRCES}/Practitioner"]}, "name": [{"text": name or "Unknown"}]}
    if reg:
        res["identifier"] = [{"type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "MD", "display": "Medical License number"}]}, "value": reg}]
    return res


def _organization(hospital):
    return {"resourceType": "Organization", "id": _id(), "meta": {"profile": [f"{NRCES}/Organization"]}, "name": hospital.name,
            "address": [{"text": hospital.address, "city": hospital.city, "state": hospital.state, "country": "IN"}]}


def _bundle(hi_type, patient_res, author_res, org_res, sections, extra, title, date_):
    code, display = DOC_TYPES[hi_type]
    comp = {
        "resourceType": "Composition", "id": _id(), "meta": {"profile": [f"{NRCES}/{hi_type}Record"]},
        "status": "final", "type": {"coding": [{"system": SNOMED, "code": code, "display": display}], "text": display},
        "subject": {"reference": _ref(patient_res)}, "date": _iso(date_), "author": [{"reference": _ref(author_res)}],
        "title": title, "custodian": {"reference": _ref(org_res)}, "section": sections,
    }
    entries = [comp, patient_res, author_res, org_res, *extra]
    return {
        "resourceType": "Bundle", "id": _id(), "meta": {"profile": [f"{NRCES}/DocumentBundle"], "lastUpdated": timezone.now().isoformat()},
        "identifier": {"system": "http://hip.in", "value": _id()}, "type": "document", "timestamp": timezone.now().isoformat(),
        "entry": [{"fullUrl": _ref(r), "resource": r} for r in entries],
    }


def _section(title, code, resources):
    return {"title": title, "code": {"coding": [{"system": SNOMED, "code": code, "display": title}]}, "entry": [{"reference": _ref(r)} for r in resources]}


def _condition(patient_res, text, icd=""):
    coding = [{"system": ICD10, "code": icd, "display": text}] if icd else []
    return {"resourceType": "Condition", "id": _id(), "meta": {"profile": [f"{NRCES}/Condition"]}, "code": {"coding": coding, "text": text},
            "subject": {"reference": _ref(patient_res)}}


def _medication_request(patient_res, prac_res, med, authored):
    name = med.get("name") or med.get("medicine") or "Medicine"
    dose_text = " ".join(str(med.get(k, "")) for k in ("dosage", "frequency", "duration", "instructions") if med.get(k))
    concept = {"text": name}
    if med.get("drug_code"):
        concept["coding"] = [{"system": SNOMED, "code": med["drug_code"], "display": name}]
    return {"resourceType": "MedicationRequest", "id": _id(), "meta": {"profile": [f"{NRCES}/MedicationRequest"]}, "status": "active", "intent": "order",
            "medicationCodeableConcept": concept, "subject": {"reference": _ref(patient_res)}, "authoredOn": _iso(authored),
            "requester": {"reference": _ref(prac_res)}, "dosageInstruction": [{"text": dose_text or "As directed"}]}


def prescription_bundle(rx):
    p, org = _patient(rx.patient), _organization(rx.hospital)
    doc = _practitioner(rx.doctor.get_full_name() if rx.doctor else "", getattr(rx.doctor, "registration_number", ""))
    meds = [_medication_request(p, doc, m if isinstance(m, dict) else {"name": str(m)}, rx.created_at) for m in (rx.medications or [])]
    cond = [_condition(p, rx.diagnosis)] if rx.diagnosis else []
    return _bundle("Prescription", p, doc, org, [_section("Prescription", "440545006", meds)], meds + cond, "Prescription record", rx.created_at)


def _observation(patient_res, name, value, unit, ref_range, flag, loinc="", when=None):
    obs = {"resourceType": "Observation", "id": _id(), "meta": {"profile": [f"{NRCES}/Observation"]}, "status": "final",
           "code": {"coding": [{"system": LOINC, "code": loinc, "display": name}] if loinc else [], "text": name},
           "subject": {"reference": _ref(patient_res)}, "effectiveDateTime": _iso(when)}
    try:
        obs["valueQuantity"] = {"value": float(value), "unit": unit}
    except (TypeError, ValueError):
        obs["valueString"] = str(value)
    if ref_range:
        obs["referenceRange"] = [{"text": ref_range}]
    interp = {"high": "H", "low": "L", "critical": "AA", "normal": "N"}.get(flag)
    if interp:
        obs["interpretation"] = [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation", "code": interp}]}]
    return obs


def lab_report_bundle(order):
    p, org = _patient(order.patient), _organization(order.hospital)
    signer = order.results.filter(finalized_by__isnull=False).select_related("finalized_by").first()
    doc = _practitioner(signer.finalized_by.get_full_name() if signer else "Laboratory", getattr(signer.finalized_by, "registration_number", "") if signer else "")
    obs = [_observation(p, r.lab_test.name, r.value, r.unit or r.lab_test.unit, r.reference_range or r.lab_test.reference_range, r.flag, r.lab_test.loinc_code, r.created_at)
           for r in order.results.select_related("lab_test")]
    report = {"resourceType": "DiagnosticReport", "id": _id(), "meta": {"profile": [f"{NRCES}/DiagnosticReportLab"]}, "status": "final",
              "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0074", "code": "LAB", "display": "Laboratory"}]}],
              "code": {"text": f"Laboratory report {order.order_number}"}, "subject": {"reference": _ref(p)}, "issued": _iso(timezone.now()),
              "performer": [{"reference": _ref(org)}], "resultsInterpreter": [{"reference": _ref(doc)}], "result": [{"reference": _ref(o)} for o in obs]}
    return _bundle("DiagnosticReport", p, doc, org, [_section("Laboratory report", "721981007", [report])], [report, *obs], "Diagnostic Report - Lab", order.ordered_at)


def imaging_report_bundle(report):
    order = report.radiology_order
    p, org = _patient(order.patient), _organization(report.hospital)
    doc = _practitioner(report.reported_by.get_full_name() if report.reported_by else "Radiology", getattr(report.reported_by, "registration_number", ""))
    extra = []
    dr = {"resourceType": "DiagnosticReport", "id": _id(), "meta": {"profile": [f"{NRCES}/DiagnosticReportImaging"]}, "status": "amended" if report.is_amended else "final",
          "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0074", "code": "RAD", "display": "Radiology"}]}],
          "code": {"text": order.procedure.name}, "subject": {"reference": _ref(p)}, "issued": _iso(report.finalized_at or report.created_at),
          "resultsInterpreter": [{"reference": _ref(doc)}], "conclusion": report.impression, "presentedForm": [{"contentType": "text/plain", "title": "Findings", "data": _b64(report.findings)}]}
    if order.study_instance_uid:
        study = {"resourceType": "ImagingStudy", "id": _id(), "meta": {"profile": [f"{NRCES}/ImagingStudy"]}, "status": "available",
                 "identifier": [{"system": "urn:dicom:uid", "value": f"urn:oid:{order.study_instance_uid}"}], "subject": {"reference": _ref(p)},
                 "modality": [{"system": "http://dicom.nema.org/resources/ontology/DCM", "code": order.procedure.modality.upper()[:2]}]}
        dr["imagingStudy"] = [{"reference": _ref(study)}]
        extra.append(study)
    return _bundle("DiagnosticReport", p, doc, org, [_section("Imaging report", "4201000179104", [dr])], [dr, *extra], "Diagnostic Report - Imaging", order.ordered_at)


def _b64(text):
    import base64

    return base64.b64encode((text or "").encode()).decode()


def discharge_summary_bundle(summary):
    adm = summary.admission
    p, org = _patient(adm.patient), _organization(summary.hospital)
    doc = _practitioner(summary.prepared_by.get_full_name() if summary.prepared_by else adm.admitting_doctor.name, getattr(summary.prepared_by, "registration_number", "") if summary.prepared_by else "")
    coding = getattr(adm, "coding", None)
    conditions = [_condition(p, summary.final_diagnosis or adm.admission_diagnosis, coding.principal_diagnosis.code if coding else "")]
    if coding:
        conditions += [_condition(p, c.title, c.code) for c in coding.secondary_diagnoses.all()]
    procs = [{"resourceType": "Procedure", "id": _id(), "meta": {"profile": [f"{NRCES}/Procedure"]}, "status": "completed", "code": {"text": summary.procedures_performed},
              "subject": {"reference": _ref(p)}}] if summary.procedures_performed else []
    meds = [_medication_request(p, doc, {"name": line.strip()}, summary.created_at) for line in (summary.discharge_medications or "").splitlines() if line.strip()]
    enc = {"resourceType": "Encounter", "id": _id(), "meta": {"profile": [f"{NRCES}/Encounter"]}, "status": "finished",
           "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "IMP", "display": "inpatient encounter"},
           "subject": {"reference": _ref(p)}, "period": {"start": _iso(adm.admitted_at), "end": _iso(adm.discharged_at)}}
    follow = {"resourceType": "CarePlan", "id": _id(), "meta": {"profile": [f"{NRCES}/CarePlan"]}, "status": "active", "intent": "plan",
              "subject": {"reference": _ref(p)}, "description": summary.follow_up_instructions or "As advised"}
    sections = [
        _section("Diagnosis", "439401001", conditions), _section("Procedures", "1003640003", procs),
        _section("Medications", "1003606003", meds), _section("Care plan", "734163000", [follow]),
    ]
    return _bundle("DischargeSummary", p, doc, org, sections, [enc, *conditions, *procs, *meds, follow], "Discharge Summary", summary.created_at)


def op_consultation_bundle(encounter):
    p, org = _patient(encounter.patient), _organization(encounter.hospital)
    doc = _practitioner(encounter.doctor.name)
    conditions = [_condition(p, d.description, d.icd_code) for d in encounter.diagnoses.all()] if hasattr(encounter, "diagnoses") else []
    vitals = []
    for v in encounter.vitals_readings.all():
        for name, loinc, val, unit in (("Pulse", "8867-4", v.pulse, "/min"), ("Systolic BP", "8480-6", v.bp_systolic, "mm[Hg]"),
                                       ("Diastolic BP", "8462-4", v.bp_diastolic, "mm[Hg]"), ("Body temperature", "8310-5", v.temperature_c, "Cel"), ("Body weight", "29463-7", v.weight_kg, "kg")):
            if val is not None:
                vitals.append(_observation(p, name, val, unit, "", None, loinc, v.recorded_at))
    allergies = [{"resourceType": "AllergyIntolerance", "id": _id(), "meta": {"profile": [f"{NRCES}/AllergyIntolerance"]}, "code": {"text": a.allergen},
                  "patient": {"reference": _ref(p)}, "criticality": "high" if a.severity == "severe" else "low"} for a in encounter.patient.allergies.filter(status="active")]
    sections = [_section("Medical history / diagnosis", "371529009", conditions), _section("Physical examination", "425044008", vitals), _section("Allergies", "722446000", allergies)]
    return _bundle("OPConsultation", p, doc, org, sections, [*conditions, *vitals, *allergies], "OP Consultation Record", encounter.created_at)


def validate_document_bundle(bundle):
    """Structural checks the ABDM validator enforces first; returns a list
    of problems (empty = OK)."""
    problems = []
    if bundle.get("resourceType") != "Bundle" or bundle.get("type") != "document":
        problems.append("Bundle.type must be 'document'")
    entries = bundle.get("entry") or []
    if not entries or entries[0]["resource"].get("resourceType") != "Composition":
        problems.append("First entry must be a Composition")
    urls = {e["fullUrl"] for e in entries}
    for e in entries:
        for ref in _walk_refs(e["resource"]):
            if ref.startswith("urn:uuid:") and ref not in urls:
                problems.append(f"Unresolved reference {ref} in {e['resource']['resourceType']}")
    return problems


def _walk_refs(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "reference" and isinstance(v, str):
                yield v
            else:
                yield from _walk_refs(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_refs(v)
