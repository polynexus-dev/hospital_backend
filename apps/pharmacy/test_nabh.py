from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import User
from apps.clinical.models import Allergy
from apps.patients.models import Patient
from apps.pharmacy.models import EmergencyMedicationStock, Medicine, MedicineBatch, StockOutEvent


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Anil", mobile="9000000301", date_of_birth=date(1975, 3, 3))


@pytest.fixture
def second_user(hospital):
    return User.objects.create_user(email="pharm2@test-hospital.example", password="x", hospital=hospital)


def _batch(hospital, med, qty=100, days=365, number="B1"):
    return MedicineBatch.objects.create(hospital=hospital, medicine=med, batch_number=number, expiry_date=timezone.localdate() + timedelta(days=days), quantity_available=qty)


def test_dispense_blocks_expired_and_allergy_without_override(auth_client, hospital, patient):
    amox = Medicine.objects.create(hospital=hospital, name="Amoxicillin 500", generic_name="amoxicillin")
    old = _batch(hospital, amox, days=-1, number="OLD")
    assert auth_client.post("/api/v1/pharmacy/dispense-records/", {"batch": old.pk, "quantity": 1, "patient": patient.pk}, format="json").json()["code"] == "expired"
    Allergy.objects.create(hospital=hospital, patient=patient, allergen="penicillin", severity="severe")
    good = _batch(hospital, amox, number="NEW")
    res = auth_client.post("/api/v1/pharmacy/dispense-records/", {"batch": good.pk, "quantity": 1, "patient": patient.pk}, format="json")
    assert res.status_code == 409 and res.json()["code"] == "safety_alert"
    res = auth_client.post("/api/v1/pharmacy/dispense-records/", {"batch": good.pk, "quantity": 1, "patient": patient.pk, "override_reason": "Desensitised under supervision"}, format="json")
    assert res.status_code == 201 and res.json()["override_reason"]


def test_high_risk_needs_second_person_and_non_formulary_flagged(auth_client, hospital, patient, second_user):
    insulin = Medicine.objects.create(hospital=hospital, name="Insulin regular", is_high_risk=True, is_formulary=False)
    b = _batch(hospital, insulin)
    assert auth_client.post("/api/v1/pharmacy/dispense-records/", {"batch": b.pk, "quantity": 1, "patient": patient.pk}, format="json").json()["code"] == "double_check_required"
    res = auth_client.post("/api/v1/pharmacy/dispense-records/", {"batch": b.pk, "quantity": 1, "patient": patient.pk, "verified_by": second_user.pk}, format="json").json()
    assert res["verified_by"] == second_user.pk and res["is_non_formulary"] is True and res["dispensed_at"]


def test_reorder_expiry_and_recall(auth_client, hospital, patient):
    para = Medicine.objects.create(hospital=hospital, name="Paracetamol", reorder_level=50)
    b = _batch(hospital, para, qty=10, days=20)
    assert any(r["name"] == "Paracetamol" for r in auth_client.get("/api/v1/pharmacy/medicines/reorder_alerts/").json())
    assert any(r["batch_number"] == "B1" for r in auth_client.get("/api/v1/pharmacy/batches/expiring/?days=30").json())
    auth_client.post("/api/v1/pharmacy/dispense-records/", {"batch": b.pk, "quantity": 2, "patient": patient.pk}, format="json")
    recall = auth_client.post("/api/v1/pharmacy/recalls/", {"medicine": para.pk, "reason": "NSQ notice"}, format="json").json()
    b.refresh_from_db()
    assert b.is_quarantined
    assert auth_client.get(f"/api/v1/pharmacy/recalls/{recall['id']}/affected_patients/").json()[0]["patient__uhid"] == patient.uhid
    assert auth_client.post("/api/v1/pharmacy/dispense-records/", {"batch": b.pk, "quantity": 1}, format="json").status_code == 409


def test_indent_issue_fefo_and_emergency_stockout(auth_client, hospital):
    med = Medicine.objects.create(hospital=hospital, name="Ringer lactate")
    late = _batch(hospital, med, qty=5, days=300, number="LATE")
    soon = _batch(hospital, med, qty=3, days=30, number="SOON")
    ind = auth_client.post("/api/v1/pharmacy/indents/", {"department": "Ward A", "items": [{"medicine": med.pk, "quantity": 4}]}, format="json").json()
    res = auth_client.post(f"/api/v1/pharmacy/indents/{ind['id']}/issue/").json()
    assert res["status"] == "issued"
    soon.refresh_from_db(); late.refresh_from_db()
    assert soon.quantity_available == 0 and late.quantity_available == 4

    adr = Medicine.objects.create(hospital=hospital, name="Adrenaline 1mg", is_emergency=True)
    stock = auth_client.post("/api/v1/pharmacy/emergency-stock/", {"location": "ER cart 1", "medicine": adr.pk, "par_level": 5, "current_quantity": 5}, format="json").json()
    auth_client.post(f"/api/v1/pharmacy/emergency-stock/{stock['id']}/check/", {"current_quantity": 0}, format="json")
    assert StockOutEvent.objects.filter(medicine=adr, is_emergency_medication=True, resolved_at__isnull=True).exists()


def test_reconciliation_requires_decisions(auth_client, patient):
    bad = auth_client.post("/api/v1/pharmacy/reconciliations/", {"patient": patient.pk, "stage": "admission", "items": [{"name": "Metformin"}]}, format="json")
    assert bad.status_code == 400
    ok = auth_client.post("/api/v1/pharmacy/reconciliations/", {"patient": patient.pk, "stage": "admission", "items": [{"name": "Metformin", "decision": "stop"}, {"name": "Amlodipine", "decision": "continue"}]}, format="json").json()
    assert ok["discrepancies_found"] == 1


def test_emar_wrong_patient_blocked(auth_client, hospital, patient):
    from apps.appointments.models import Doctor
    from apps.facilities.models import Bed, Room, Ward
    from apps.ipd.services import admit_patient

    ward = Ward.objects.create(hospital=hospital, name="W")
    bed = Bed.objects.create(hospital=hospital, room=Room.objects.create(hospital=hospital, ward=ward, room_number="1"), bed_number="1")
    adm = admit_patient(hospital=hospital, patient=patient, admitting_doctor=Doctor.objects.create(hospital=hospital, name="Dr"), bed=bed, admission_type="planned", department=None, admission_diagnosis="", source_encounter=None)
    base = {"admission": adm.pk, "medication_name": "Ceftriaxone 1g", "dose": "1g", "identity_method": "wristband_scan"}
    assert auth_client.post("/api/v1/nursing/medication-administrations/", {**base, "scanned_identifier": "WRONG-1"}, format="json").status_code == 400
    res = auth_client.post("/api/v1/nursing/medication-administrations/", {**base, "scanned_identifier": patient.uhid}, format="json").json()
    assert res["identity_verified"] is True and res["patient"] == patient.pk
