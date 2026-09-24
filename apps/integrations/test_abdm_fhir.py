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


def test_prescription_bundle(auth_client, hospital, patient):
    rx = Prescription.objects.create(hospital=hospital, patient=patient, diagnosis="Hypertension", medications=[{"name": "Amlodipine 5mg", "frequency": "OD"}])
    b = auth_client.get(f"/api/v1/abdm-fhir/prescription/{rx.pk}/").json()
    assert validate_document_bundle(b) == []
    assert b["entry"][0]["resource"]["type"]["coding"][0]["code"] == "440545006"
    assert _resources(b, "MedicationRequest")[0]["medicationCodeableConcept"]["text"] == "Amlodipine 5mg"
    assert _resources(b, "Patient")[0]["identifier"][0]["value"] == patient.uhid


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
