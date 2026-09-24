from datetime import date

import pytest

from apps.clinical import scoring
from apps.clinical.models import Allergy, ClinicalAlert, DigitalSignature, NotifiableDiseaseReport
from apps.clinical.signatures import document_hash
from apps.patients.models import Patient, Prescription


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Jane", last_name="Smith", mobile="9000000002", date_of_birth=date(1966, 1, 1))


@pytest.fixture
def minor(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Tiny", mobile="9000000003", date_of_birth=date(2020, 5, 1))


def test_defaults_seeded_for_new_hospital(hospital):
    from apps.clinical.models import DrugInteraction, NotifiableDisease

    assert DrugInteraction.objects.filter(hospital=hospital, drug_a="warfarin").exists()
    assert NotifiableDisease.objects.filter(hospital=hospital, name="Dengue").exists()


@pytest.mark.parametrize("tool,answers,level", [
    ("morse_fall", {"history_of_falling": True, "secondary_diagnosis": True, "iv_or_heparin_lock": True}, "high"),
    ("morse_fall", {}, "low"),
    ("braden", {"sensory_perception": 1, "moisture": 1, "activity": 1, "mobility": 1, "nutrition": 1, "friction_shear": 1}, "very_high"),
    ("caprini", {"age_75_plus": True, "history_of_vte": True}, "highest"),
    ("news2", {"respiratory_rate": 28, "spo2": 90, "systolic_bp": 88, "heart_rate": 135}, "high"),
])
def test_risk_scores(tool, answers, level):
    assert scoring.score(tool, answers)[1] == level


def test_high_risk_assessment_raises_nursing_alert(auth_client, patient):
    res = auth_client.post("/api/v1/clinical/risk-assessments/", {
        "patient": patient.pk, "tool": "morse_fall", "answers": {"history_of_falling": True, "gait": "impaired", "secondary_diagnosis": True},
        "score": 0,  # ignored — server computes
    }, format="json")
    assert res.status_code == 201, res.json()
    assert res.json()["score"] == 60 and res.json()["risk_level"] == "high"
    assert ClinicalAlert.objects.filter(patient=patient, alert_type="risk", target_department="nursing").exists()


def test_cdss_flags_allergy_interaction_and_duplicate(auth_client, hospital, patient, user):
    Allergy.objects.create(hospital=hospital, patient=patient, allergen="penicillin", severity="severe")
    Prescription.objects.create(hospital=hospital, patient=patient, diagnosis="AF", medications=[{"name": "Warfarin 5mg"}])
    res = auth_client.post("/api/v1/clinical/cdss/check/", {
        "patient": patient.pk, "medications": [{"name": "Amoxicillin 500mg"}, {"name": "Aspirin 75mg"}, {"name": "Warfarin 2mg"}], "persist": True,
    }, format="json")
    assert res.status_code == 200
    types = {a["alert_type"] for a in res.json()["alerts"]}
    assert {"allergy", "interaction", "duplicate_order"} <= types
    assert res.json()["blocking"] is True
    assert ClinicalAlert.objects.filter(patient=patient, alert_type="allergy").exists()


def test_cdss_contraindication_uses_diagnosis(auth_client, hospital, patient):
    from apps.clinical.models import ClinicalAssessment

    ClinicalAssessment.objects.create(hospital=hospital, patient=patient, provisional_diagnosis="CKD stage 4 (N18.4)")
    res = auth_client.post("/api/v1/clinical/cdss/check/", {"patient": patient.pk, "medications": [{"name": "Metformin 500"}]}, format="json")
    assert any(a["alert_type"] == "contraindication" for a in res.json()["alerts"])


def test_critical_alert_override_requires_reason(auth_client, hospital, patient):
    alert = ClinicalAlert.objects.create(hospital=hospital, patient=patient, alert_type="allergy", severity="critical", title="x", message="y")
    assert auth_client.post(f"/api/v1/clinical/alerts/{alert.pk}/acknowledge/", {}, format="json").status_code == 400
    assert auth_client.post(f"/api/v1/clinical/alerts/{alert.pk}/acknowledge/", {"override_reason": "Desensitised"}, format="json").status_code == 200


def test_minor_consent_requires_guardian(auth_client, minor):
    base = {"patient": minor.pk, "consent_type": "procedure", "procedure_name": "Tonsillectomy"}
    assert auth_client.post("/api/v1/clinical/consents/", base, format="json").status_code == 400
    res = auth_client.post("/api/v1/clinical/consents/", {**base, "given_by": "guardian", "guardian_name": "Mother", "guardian_relation": "mother"}, format="json")
    assert res.status_code == 201 and res.json()["patient_is_minor"] is True


def test_assessment_with_notifiable_diagnosis_opens_report(auth_client, patient):
    res = auth_client.post("/api/v1/clinical/assessments/", {"patient": patient.pk, "provisional_diagnosis": "Dengue fever with warning signs"}, format="json")
    assert res.status_code == 201, res.json()
    report = NotifiableDiseaseReport.objects.get(patient=patient)
    assert report.disease.name == "Dengue" and report.status == "pending"
    res = auth_client.post(f"/api/v1/clinical/notifiable-reports/{report.pk}/mark_reported/", {"reference_number": "IHIP-123"}, format="json")
    assert res.json()["status"] == "reported"


def test_template_required_fields_enforced(auth_client, hospital, patient):
    from apps.clinical.models import AssessmentTemplate

    tpl = AssessmentTemplate.objects.get(hospital=hospital, category="antenatal")
    res = auth_client.post("/api/v1/clinical/assessments/", {"patient": patient.pk, "template": tpl.pk, "data": {}}, format="json")
    assert res.status_code == 400
    res = auth_client.post("/api/v1/clinical/assessments/", {"patient": patient.pk, "template": tpl.pk, "data": {"lmp": "2026-06-01", "gravida_para": "G2P1L1A0"}}, format="json")
    assert res.status_code == 201 and res.json()["category"] == "antenatal"


def test_sign_prescription_and_detect_tampering(auth_client, hospital, patient, user):
    user.set_password("Sign$Passw0rd1")
    user.save()
    rx = Prescription.objects.create(hospital=hospital, patient=patient, diagnosis="URTI", medications=[{"name": "Paracetamol"}])
    assert auth_client.post("/api/v1/clinical/sign/", {"document_type": "prescription", "document_id": rx.pk, "method": "password", "password": "bad"}, format="json").status_code == 400
    res = auth_client.post("/api/v1/clinical/sign/", {"document_type": "prescription", "document_id": rx.pk, "method": "password", "password": "Sign$Passw0rd1"}, format="json")
    assert res.status_code == 201 and res.json()["is_valid"] is True
    Prescription.objects.filter(pk=rx.pk).update(notes="edited after signing")
    rx.refresh_from_db()
    sig = DigitalSignature.objects.get(object_id=str(rx.pk))
    assert document_hash(rx) != sig.document_hash


def test_handover_acknowledged_by_receiver(auth_client, hospital, patient, user):
    from apps.appointments.models import Doctor
    from apps.facilities.models import Bed, Room, Ward
    from apps.ipd.models import Admission

    ward = Ward.objects.create(hospital=hospital, name="W1")
    room = Room.objects.create(hospital=hospital, ward=ward, room_number="1")
    bed = Bed.objects.create(hospital=hospital, room=room, bed_number="1")
    doctor = Doctor.objects.create(hospital=hospital, name="Dr A")
    adm = Admission.objects.create(hospital=hospital, patient=patient, admitting_doctor=doctor, bed=bed)
    res = auth_client.post("/api/v1/clinical/handovers/", {"admission": adm.pk, "shift": "night", "situation": "Post-op day 1, stable"}, format="json")
    assert res.status_code == 201, res.json()
    res = auth_client.post(f"/api/v1/clinical/handovers/{res.json()['id']}/acknowledge/", format="json")
    assert res.json()["acknowledged_at"] is not None


def test_clinical_summary_links_records_to_uhid(auth_client, hospital, patient):
    Allergy.objects.create(hospital=hospital, patient=patient, allergen="sulfa")
    res = auth_client.get(f"/api/v1/clinical/patients/{patient.pk}/summary/")
    assert res.status_code == 200 and res.json()["patient"]["uhid"] == patient.uhid
    assert res.json()["allergies"][0]["allergen"] == "sulfa"


def test_barthel_total_and_reassessment_delta(auth_client, patient):
    first = auth_client.post("/api/v1/clinical/functional-assessments/", {"patient": patient.pk, "discipline": "physiotherapy", "scale": "barthel", "scores": {"feeding": 5, "mobility": 5}}, format="json").json()
    assert first["total"] == 10
    second = auth_client.post("/api/v1/clinical/functional-assessments/", {"patient": patient.pk, "discipline": "physiotherapy", "scale": "barthel", "scores": {"feeding": 10, "mobility": 10}, "previous": first["id"]}, format="json").json()
    assert second["change_from_previous"] == 10


def test_cross_tenant_patient_rejected(auth_client, other_hospital):
    foreign = Patient.objects.create(hospital=other_hospital, first_name="X", mobile="9000000009")
    res = auth_client.post("/api/v1/clinical/allergies/", {"patient": foreign.pk, "allergen": "latex"}, format="json")
    assert res.status_code == 400
