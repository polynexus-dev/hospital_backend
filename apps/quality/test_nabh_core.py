"""Tests for NABH HIS/EMR Core elements that had code but no test proving them.
Each test names the element it demonstrates (picked up by docs/nabh/build_matrix.py)."""
from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.appointments.models import Doctor
from apps.facilities.models import Bed, Room, Ward
from apps.ipd.services import admit_patient
from apps.patients.models import Patient


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Meera", last_name="Kulkarni", mobile="9000000301", date_of_birth=date(1975, 6, 1))


@pytest.fixture
def doctor(hospital, department):
    return Doctor.objects.create(hospital=hospital, department=department, name="Dr Mehta")


@pytest.fixture
def bed(hospital):
    ward = Ward.objects.create(hospital=hospital, name="Ward B")
    room = Room.objects.create(hospital=hospital, ward=ward, room_number="201")
    return Bed.objects.create(hospital=hospital, room=room, bed_number="1")


@pytest.mark.django_db
def test_aac_3_e_sample_label_prints_as_a_barcode_pdf(auth_client, hospital, patient):
    """NABH AAC.3.e: sample label printing."""
    from apps.laboratory.models import LabTest

    test = LabTest.objects.create(hospital=hospital, name="Haemoglobin", code="HB")
    order = auth_client.post("/api/v1/laboratory/orders/", {"patient": patient.pk, "ordered_tests": [test.pk]}, format="json").json()
    sample = auth_client.post("/api/v1/laboratory/samples/", {"lab_order": order["id"], "sample_type": "blood"}, format="json")
    assert sample.status_code == 201, sample.data
    label = auth_client.get(f"/api/v1/laboratory/samples/{sample.data['id']}/label/")
    assert label.status_code == 200 and label.content[:4] == b"%PDF"


@pytest.mark.django_db
def test_aac_5_e_and_aac_6_b_care_team_and_discharge_due_list(auth_client, hospital, patient, doctor, bed, department):
    """NABH AAC.5.e: treating practitioners designated; AAC.6.b: list of patients due for discharge."""
    consultant = Doctor.objects.create(hospital=hospital, department=department, name="Dr Rao")
    admission = admit_patient(hospital=hospital, patient=patient, admitting_doctor=doctor, bed=bed)
    res = auth_client.patch(f"/api/v1/ipd/admissions/{admission.pk}/", {"care_team": [doctor.pk, consultant.pk], "expected_discharge_date": str(timezone.localdate())}, format="json")
    assert res.status_code == 200, res.data
    admission.refresh_from_db()
    assert set(admission.care_team.values_list("pk", flat=True)) == {doctor.pk, consultant.pk}
    due = auth_client.get("/api/v1/ipd/admissions/due_for_discharge/").json()
    assert [row["id"] for row in due] == [admission.pk] and due[0]["uhid"] == patient.uhid


@pytest.mark.django_db
def test_aac_4_h_signed_radiology_report_cannot_be_edited(auth_client, hospital, patient):
    """NABH AAC.4.h: the final report is non-editable once signed; changes go through a flagged amendment (AAC.4.f/i)."""
    from apps.radiology.models import RadiologyProcedure

    xr = RadiologyProcedure.objects.create(hospital=hospital, name="Chest X-ray", modality="xray", duration_minutes=10)
    order = auth_client.post("/api/v1/radiology/orders/", {"patient": patient.pk, "procedure": xr.pk}, format="json").json()
    rep = auth_client.post("/api/v1/radiology/reports/", {"radiology_order": order["id"], "findings": "Clear", "impression": "Normal"}, format="json").json()
    assert auth_client.post(f"/api/v1/radiology/reports/{rep['id']}/verify/").status_code == 200
    edit = auth_client.patch(f"/api/v1/radiology/reports/{rep['id']}/", {"impression": "Changed quietly"}, format="json")
    assert edit.status_code in (400, 403)
    assert auth_client.get(f"/api/v1/radiology/reports/{rep['id']}/").json()["impression"] == "Normal"


@pytest.mark.django_db
def test_aac_4_m_imaging_report_is_shared_with_the_abha_number(hospital, patient):
    """NABH AAC.4.m: radiology reports are linked to the patient's ABHA in the ABDM FHIR bundle."""
    from apps.abdm.models import AbhaLink
    from apps.integrations.abdm_fhir import imaging_report_bundle, validate_document_bundle
    from apps.radiology.models import RadiologyOrder, RadiologyProcedure, RadiologyReport

    AbhaLink.objects.create(hospital=hospital, patient=patient, status="linked", verification_method="aadhaar_otp", abha_number="91-1234-5678-9012")
    xr = RadiologyProcedure.objects.create(hospital=hospital, name="Chest X-ray", modality="xray")
    order = RadiologyOrder.objects.create(hospital=hospital, patient=patient, procedure=xr)
    report = RadiologyReport.objects.create(hospital=hospital, radiology_order=order, findings="Clear", impression="Normal")
    bundle = imaging_report_bundle(report)
    assert validate_document_bundle(bundle) == []
    patients = [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "Patient"]
    assert {"system": "https://healthid.ndhm.gov.in", "value": "91-1234-5678-9012"} in patients[0]["identifier"]


@pytest.mark.django_db
def test_fpm_1_f_quality_feedback_on_purchased_goods(auth_client):
    """NABH FPM.1.f: feedback on the quality of purchased goods."""
    res = auth_client.post("/api/v1/inventory/supplier-ratings/", {"vendor_name": "MedSupply", "quality": 2, "delivery_timeliness": 4, "remarks": "Torn glove packs"}, format="json")
    assert res.status_code == 201, res.data
    assert auth_client.get("/api/v1/inventory/supplier-ratings/").json()["results"][0]["quality"] == 2


@pytest.mark.django_db
def test_fpm_4_a_insurance_details_eligibility_and_coverage(auth_client, patient):
    """NABH FPM.4.a: the patient's insurance, eligibility and coverage are captured."""
    body = {
        "patient": patient.pk, "insurer": "Star Health", "policy_number": "SH-998877", "sum_insured": "500000",
        "valid_from": str(date.today()), "valid_to": str(date.today() + timedelta(days=365)),
        "coverage_notes": "Room rent cap 1%; 10% co-pay", "eligibility_status": "eligible",
    }
    res = auth_client.post("/api/v1/finance/insurance-policies/", body, format="json")
    assert res.status_code == 201, res.data
    assert res.data["eligibility_status"] == "eligible" and res.data["coverage_notes"].startswith("Room rent")


@pytest.mark.django_db
def test_hrm_1_b_each_staff_member_has_a_unique_id_and_a_role(auth_client, user):
    """NABH HRM.1.b: unique staff IDs, linked to the user and their role."""
    first = auth_client.post("/api/v1/hr/employees/", {"employee_code": "EMP-101", "designation": "Staff Nurse", "user": user.pk}, format="json")
    assert first.status_code == 201, first.data
    again = auth_client.post("/api/v1/hr/employees/", {"employee_code": "EMP-101", "designation": "Clerk"}, format="json")
    assert again.status_code == 400
    assert user.role is not None


@pytest.mark.django_db
def test_dom_2_a_release_notes_reach_every_user(auth_client):
    """NABH DOM.2.a: patches and updates are announced to users (release notes)."""
    from apps.governance.models import ReleaseNote

    ReleaseNote.objects.create(version="1.0.2", kind="security", summary="Security patch for session handling", released_on=date.today())
    notes = auth_client.get("/api/v1/governance/release-notes/").json()
    rows = notes["results"] if isinstance(notes, dict) else notes
    assert rows[0]["version"] == "1.0.2" and rows[0]["kind"] == "security"
