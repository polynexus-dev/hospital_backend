from datetime import date

import pytest
from django.utils import timezone

from apps.integrations.abdm_fhir import validate_document_bundle
from apps.patients.models import Patient, Prescription


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Ganesh", mobile="9000000701", date_of_birth=date(1980, 1, 1), gender="male")


def _resources(bundle, rtype):
    return [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == rtype]


# NABH COP.1.h (e-prescription), IMS.1.a
def test_prescription_bundle(auth_client, hospital, patient):
    rx = Prescription.objects.create(hospital=hospital, patient=patient, diagnosis="Hypertension", medications=[{"name": "Amlodipine 5mg", "frequency": "OD"}])
    b = auth_client.get(f"/api/v1/abdm-fhir/prescription/{rx.pk}/").json()
    assert validate_document_bundle(b) == []
    assert b["entry"][0]["resource"]["type"]["coding"][0]["code"] == "440545006"
    assert _resources(b, "MedicationRequest")[0]["medicationCodeableConcept"]["text"] == "Amlodipine 5mg"
    assert _resources(b, "Patient")[0]["identifier"][0]["value"] == patient.uhid


# NABH AAC.3.n (lab reports shared via ABHA bundles), IMS.1.f, IMS.1.a
def test_lab_bundle_with_loinc(auth_client, hospital, patient):
    from apps.laboratory.models import LabOrder, LabResult, LabTest

    t = LabTest.objects.create(hospital=hospital, name="Haemoglobin", loinc_code="718-7", unit="g/dL", ref_low=12, ref_high=16)
    o = LabOrder.objects.create(hospital=hospital, patient=patient)
    LabResult.objects.create(hospital=hospital, lab_order=o, lab_test=t, value="10.2", flag="low")
    b = auth_client.get(f"/api/v1/abdm-fhir/lab/{o.pk}/").json()
    assert validate_document_bundle(b) == []
    obs = _resources(b, "Observation")[0]
    assert obs["code"]["coding"][0] == {"system": "http://loinc.org", "code": "718-7", "display": "Haemoglobin"}
    assert obs["valueQuantity"]["value"] == 10.2 and obs["interpretation"][0]["coding"][0]["code"] == "L"


# NABH AAC.6.f (discharge summary linked to ABHA)
def test_discharge_bundle_uses_icd10_coding(auth_client, hospital, patient):
    from apps.appointments.models import Doctor
    from apps.facilities.models import Bed, Room, Ward
    from apps.ipd.models import DischargeSummary
    from apps.ipd.services import admit_patient
    from apps.mrd.models import CodingRecord, ICD10Code

    bed = Bed.objects.create(hospital=hospital, room=Room.objects.create(hospital=hospital, ward=Ward.objects.create(hospital=hospital, name="W"), room_number="1"), bed_number="1")
    adm = admit_patient(hospital=hospital, patient=patient, admitting_doctor=Doctor.objects.create(hospital=hospital, name="Dr"), bed=bed, admission_type="planned", department=None, admission_diagnosis="Pneumonia", source_encounter=None)
    ds = DischargeSummary.objects.create(hospital=hospital, admission=adm, final_diagnosis="Community-acquired pneumonia", discharge_medications="Amoxiclav 625 TDS\nParacetamol SOS")
    CodingRecord.objects.create(hospital=hospital, admission=adm, principal_diagnosis=ICD10Code.objects.get(code="J18.9"))
    b = auth_client.get(f"/api/v1/abdm-fhir/discharge/{ds.pk}/").json()
    assert validate_document_bundle(b) == []
    assert _resources(b, "Condition")[0]["code"]["coding"][0]["code"] == "J18.9"
    assert len(_resources(b, "MedicationRequest")) == 2


def test_unknown_type_and_tenant_isolation(auth_client, other_hospital):
    other = Patient.objects.create(hospital=other_hospital, first_name="X", mobile="9000000702")
    rx = Prescription.objects.create(hospital=other_hospital, patient=other, diagnosis="x")
    assert auth_client.get(f"/api/v1/abdm-fhir/prescription/{rx.pk}/").status_code == 404
    assert auth_client.get("/api/v1/abdm-fhir/xray/1/").status_code == 400


# --- Extended (IMS.1.b) and advanced (IMS.1.c) ABDM record types ----------------


def _get_bundle(auth_client, hi_type, pk):
    res = auth_client.get(f"/api/v1/abdm-fhir/{hi_type}/{pk}/")
    assert res.status_code == 200, res.json()
    bundle = res.json()
    assert validate_document_bundle(bundle) == []
    return bundle


# NABH IMS.1.b
def test_immunization_record_bundle(auth_client, hospital, patient):
    imm = auth_client.post("/api/v1/clinical/immunizations/", {"patient": patient.pk, "vaccine": "Td (tetanus-diphtheria)", "dose_number": 2,
                                                              "lot_number": "TD-881", "route": "IM", "site": "left deltoid",
                                                              "next_due_on": str(timezone.localdate() + timezone.timedelta(days=180))}, format="json")
    assert imm.status_code == 201, imm.json()
    b = _get_bundle(auth_client, "immunization", imm.json()["id"])
    assert b["entry"][0]["resource"]["type"]["coding"][0]["code"] == "41000179103"
    res = _resources(b, "Immunization")[0]
    assert res["vaccineCode"]["text"] == "Td (tetanus-diphtheria)" and res["lotNumber"] == "TD-881" and res["protocolApplied"][0]["doseNumberPositiveInt"] == 2
    assert _resources(b, "ImmunizationRecommendation")[0]["recommendation"][0]["doseNumberPositiveInt"] == 3


# NABH IMS.1.b
def test_wellness_record_bundle(auth_client, hospital, patient):
    import datetime

    from apps.appointments.models import Doctor, Slot
    from apps.appointments.services import book_appointment, check_in
    from apps.opd.models import Encounter, VitalsReading

    doctor = Doctor.objects.create(hospital=hospital, name="Dr Well")
    slot = Slot.objects.create(hospital=hospital, doctor=doctor, date=datetime.date.today(), start_time=datetime.time(10, 0), end_time=datetime.time(10, 15))
    enc = Encounter.objects.get(appointment=check_in(book_appointment(patient=patient, slot=slot)))
    v = VitalsReading.objects.create(hospital=hospital, encounter=enc, pulse=72, bp_systolic=118, bp_diastolic=76, spo2=98, height_cm=170, weight_kg=65)
    b = _get_bundle(auth_client, "wellness", v.pk)
    codes = {o["code"]["coding"][0]["code"]: o["valueQuantity"]["value"] for o in _resources(b, "Observation")}
    assert codes["8867-4"] == 72 and codes["59408-5"] == 98 and codes["39156-5"] == 22.5
    assert [s["title"] for s in b["entry"][0]["resource"]["section"]] == ["Vital Signs", "Body Measurement"]


# NABH IMS.1.b
def test_health_document_record_bundle(auth_client, hospital, patient, settings, tmp_path):
    import base64

    from django.core.files.uploadedfile import SimpleUploadedFile

    from apps.patients.models import Document

    settings.MEDIA_ROOT = tmp_path
    doc = Document.objects.create(hospital=hospital, patient=patient, category="report", title="Outside MRI", file=SimpleUploadedFile("mri.pdf", b"%PDF-1.4 test", content_type="application/pdf"))
    b = _get_bundle(auth_client, "document", doc.pk)
    att = _resources(b, "DocumentReference")[0]["content"][0]["attachment"]
    assert att["contentType"] == "application/pdf" and base64.b64decode(att["data"]) == b"%PDF-1.4 test"
    assert b["entry"][0]["resource"]["type"]["coding"][0]["code"] == "419891008"


# NABH IMS.1.c
def test_invoice_record_bundle(auth_client, hospital, patient):
    from decimal import Decimal

    from apps.billing.models import Bill, BillItem, Payment

    bill = Bill.objects.create(hospital=hospital, patient=patient, total_amount=Decimal("1180"), net_amount=Decimal("1180"), status="issued")
    BillItem.objects.create(bill=bill, description="Consultation", quantity=1, unit_price=Decimal("1000"), total_price=Decimal("1180"), gst_rate=Decimal("18"), tax_amount=Decimal("180"))
    Payment.objects.create(hospital=hospital, bill=bill, amount=Decimal("500"), payment_method="upi")
    b = _get_bundle(auth_client, "invoice", bill.pk)
    inv = _resources(b, "Invoice")[0]
    assert inv["status"] == "issued" and inv["totalNet"] == {"value": 1180.0, "currency": "INR"}
    assert [c["type"] for c in inv["lineItem"][0]["priceComponent"]] == ["base", "tax"]
    assert _resources(b, "ChargeItem")[0]["code"]["text"] == "Consultation"
