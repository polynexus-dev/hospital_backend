"""MedNet M2.22 EMR: OPD triage recorded by the nurse and corrected by the
doctor in place; numeric pain scale with interventions by score."""
import datetime

import pytest

from apps.accounts.models import Role, User, assign_role
from apps.appointments.models import Doctor, Slot
from apps.appointments.services import book_appointment, check_in
from apps.clinical import scoring
from apps.clinical.models import ClinicalAlert, RiskAssessment
from apps.opd.models import Encounter, OPDTriage, VitalsReading
from apps.patients.models import Patient

T = "/api/v1/opd/triage/"


@pytest.fixture
def encounter(hospital, department):
    doctor = Doctor.objects.create(hospital=hospital, department=department, name="Mehta")
    slot = Slot.objects.create(hospital=hospital, doctor=doctor, date=datetime.date.today(), start_time=datetime.time(10, 0), end_time=datetime.time(10, 15))
    patient = Patient.objects.create(hospital=hospital, first_name="Sunita", mobile="+919000000601")
    return Encounter.objects.get(appointment=check_in(book_appointment(patient=patient, slot=slot)))


@pytest.fixture
def nurse_client(api_client, hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Staff Nurse", template=Role.Template.NURSE)
    u = User.objects.create_user(email="nurse.opd@test-hospital.example", password="x", hospital=hospital, department=department, first_name="Archana", last_name="Naik")
    assign_role(u, role)
    api_client.force_authenticate(u)
    yield api_client
    api_client.force_authenticate(None)


def test_numeric_pain_scale_levels_and_interventions():
    assert scoring.score("numeric_pain", {"score": 0}) == (0, "none")
    assert scoring.score("numeric_pain", {"score": "3"}) == (3, "mild")
    assert scoring.score("numeric_pain", {"score": 6}) == (6, "moderate")
    assert scoring.score("numeric_pain", {"score": 9}) == (9, "severe")
    assert scoring.score("numeric_pain", {"score": 15}) == (10, "severe")  # clamped
    assert "severe" in scoring.HIGH_RISK_LEVELS
    assert scoring.DEFAULT_INTERVENTIONS[("numeric_pain", "severe")][0] == "Inform the treating doctor now"
    assert [i["key"] for i in scoring.RISK_FORMS["numeric_pain"]][0] == "score"


def test_numeric_pain_through_risk_score_api(auth_client, encounter):
    r = auth_client.post("/api/v1/clinical/risk-assessments/", {"patient": encounter.patient_id, "tool": "numeric_pain", "answers": {"score": 8}}, format="json")
    assert r.status_code == 201, r.data
    assert r.data["score"] == 8 and r.data["risk_level"] == "severe" and r.data["is_high_risk"] is True


def test_nurse_records_triage_with_vitals_and_pain(nurse_client, encounter):
    r = nurse_client.post(T, {"encounter": encounter.pk, "chief_complaint": "Fever and body ache", "complaint_duration": "3 days",
                              "bp_systolic": 128, "bp_diastolic": 82, "pulse": 96, "temperature_c": "38.6", "spo2": 97, "pain_score": 5,
                              "allergies_checked": True}, format="json")
    assert r.status_code == 201, r.data
    assert r.data["recorded_by_name"] == "Archana Naik" and r.data["pain_level"] == "moderate" and r.data["temperature_c"] == "38.6"
    t = OPDTriage.objects.get(pk=r.data["id"])
    assert t.vitals.bp_systolic == 128 and t.vitals.encounter_id == encounter.pk
    pain = RiskAssessment.objects.get(pk=t.pain_assessment_id)
    assert pain.tool == "numeric_pain" and pain.score == 5 and pain.risk_level == "moderate"
    assert nurse_client.post(T, {"encounter": encounter.pk}, format="json").status_code == 400  # one triage per visit


def test_doctor_corrects_triage_in_place(nurse_client, user, encounter):
    from rest_framework.test import APIClient

    tid = nurse_client.post(T, {"encounter": encounter.pk, "chief_complaint": "Fever", "pulse": 96, "pain_score": 2}, format="json").data["id"]
    doctor = APIClient()
    doctor.force_authenticate(user)
    r = doctor.patch(f"{T}{tid}/", {"pulse": 88, "pain_score": 7, "chief_complaint": "Fever with chills"}, format="json")
    assert r.status_code == 200, r.data
    t = OPDTriage.objects.get(pk=tid)
    assert t.updated_by_id == user.pk and t.recorded_by.email == "nurse.opd@test-hospital.example"
    assert VitalsReading.objects.filter(encounter=encounter).count() == 1 and t.vitals.pulse == 88  # corrected, not duplicated
    assert RiskAssessment.objects.get(pk=t.pain_assessment_id).risk_level == "severe"
    assert r.data["chief_complaint"] == "Fever with chills"


def test_emergency_priority_alerts_the_doctor(nurse_client, encounter):
    nurse_client.post(T, {"encounter": encounter.pk, "chief_complaint": "Chest pain", "priority": "emergency"}, format="json")
    a = ClinicalAlert.objects.get(patient=encounter.patient, severity="critical")
    assert "emergency" in a.title.lower() and a.message == "Chest pain"


def test_receptionist_cannot_read_triage(api_client, hospital, department, encounter):
    role = Role.objects.create(hospital=hospital, department=department, name="Receptionist", template=Role.Template.RECEPTIONIST)
    u = User.objects.create_user(email="rec.t@test-hospital.example", password="x", hospital=hospital, department=department)
    assign_role(u, role)
    api_client.force_authenticate(u)
    assert api_client.get(f"{T}?encounter={encounter.pk}").status_code == 403
