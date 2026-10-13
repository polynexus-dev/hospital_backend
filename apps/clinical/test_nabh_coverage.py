"""NABH elements in registration and clinical care that had code but no test:
each test drives the feature through the API the way staff use it."""
from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.appointments.models import Doctor
from apps.clinical.models import ClinicalAlert, MedicalDevice
from apps.core.models import Hospital, HospitalGroup
from apps.facilities.models import Bed, Room, Ward
from apps.patients.models import Patient, Prescription, UHIDConfig


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Meera", last_name="Joshi", mobile="9000000201", date_of_birth=date(1985, 5, 5))


@pytest.fixture
def admission(hospital, patient):
    from apps.ipd.services import admit_patient

    ward = Ward.objects.create(hospital=hospital, name="Ward B")
    room = Room.objects.create(hospital=hospital, ward=ward, room_number="201")
    bed = Bed.objects.create(hospital=hospital, room=room, bed_number="B1")
    doctor = Doctor.objects.create(hospital=hospital, name="Dr Iyer")
    return admit_patient(hospital=hospital, patient=patient, admitting_doctor=doctor, bed=bed, admission_type="planned",
                         department=None, admission_diagnosis="Pneumonia", source_encounter=None)


# NABH AAC.1.b
def test_mobile_number_verified_by_otp(auth_client, patient, monkeypatch):
    monkeypatch.setattr("apps.patients.registration.secrets.randbelow", lambda n: 424242)
    url = f"/api/v1/patients/{patient.pk}/verify-mobile/"
    assert auth_client.post(url, {}, format="json").json()["sent"] is True
    assert auth_client.post(url, {"otp": "111111"}, format="json").status_code == 400
    assert auth_client.post(url, {"otp": "424242"}, format="json").json() == {"verified": True}
    patient.refresh_from_db()
    assert patient.mobile_verified_at is not None


# NABH AAC.1.d
def test_uhid_follows_the_hospitals_configured_format(auth_client, hospital):
    res = auth_client.patch("/api/v1/patients/uhid-config/", {"prefix": "PNX", "branch_code": "BNR", "pattern": "{PREFIX}-{BRANCH}-{YYYY}-{SEQ}", "sequence_digits": 5}, format="json")
    assert res.status_code == 200, res.json()
    p = Patient.objects.create(hospital=hospital, first_name="Kiran", last_name="Rao", mobile="9000000202")
    assert p.uhid == f"PNX-BNR-{timezone.localdate():%Y}-" + p.uhid.rsplit("-", 1)[1] and len(p.uhid.rsplit("-", 1)[1]) == 5
    assert UHIDConfig.objects.get(hospital=hospital).prefix == "PNX"


# NABH AAC.1.h
def test_visits_grouped_under_an_episode_of_care(auth_client, hospital, patient, admission):
    ep = auth_client.post("/api/v1/clinical/episodes/", {"patient": patient.pk, "condition": "Pregnancy 2026", "specialty": "Obstetrics"}, format="json").json()
    assert ep["episode_code"].startswith(f"EP-{patient.uhid}-")
    admission.episode_id = ep["id"]
    admission.save(update_fields=["episode"])
    visits = auth_client.get(f"/api/v1/clinical/episodes/{ep['id']}/visits/").json()
    assert [a["id"] for a in visits["admissions"]] == [admission.pk] and visits["encounters"] == []
    assert auth_client.post(f"/api/v1/clinical/episodes/{ep['id']}/close/").json()["status"] == "closed"


# NABH AAC.1.j
def test_records_shared_with_an_affiliate_only_with_consent(auth_client, api_client, hospital, patient, other_hospital, other_user):
    group = HospitalGroup.objects.create(name="Group")
    for h in (hospital, other_hospital):
        h.group = group
        h.save(update_fields=["group"])
    outsider = Hospital.objects.create(name="Unrelated", slug="unrelated")
    expires = (timezone.now() + timedelta(days=2)).isoformat()
    base = {"patient": patient.pk, "purpose": "Second opinion", "expires_at": expires}
    assert auth_client.post("/api/v1/clinical/record-shares/", {**base, "shared_with": outsider.pk, "patient_consent": True}, format="json").status_code == 400
    assert auth_client.post("/api/v1/clinical/record-shares/", {**base, "shared_with": other_hospital.pk}, format="json").status_code == 400
    share = auth_client.post("/api/v1/clinical/record-shares/", {**base, "shared_with": other_hospital.pk, "patient_consent": True}, format="json")
    assert share.status_code == 201, share.json()

    from apps.accounts.models import Role, assign_role

    assign_role(other_user, Role.objects.create(hospital=other_hospital, name="Doctor", template=Role.Template.ADMIN))
    api_client.force_authenticate(user=other_user)
    listed = api_client.get("/api/v1/clinical/shared-with-me/").json()
    assert [s["uhid"] for s in listed] == [patient.uhid]
    assert api_client.get(f"/api/v1/clinical/shared-with-me/{share.json()['id']}/").json()["patient"]["uhid"] == patient.uhid
    api_client.force_authenticate(user=None)


# NABH AAC.1.k
def test_specialty_referral_alerts_the_consultant_and_records_the_reply(auth_client, hospital, patient, user):
    cardiologist = Doctor.objects.create(hospital=hospital, name="Dr Heart", user=user)
    ref = auth_client.post("/api/v1/clinical/referrals/", {"patient": patient.pk, "to_doctor": cardiologist.pk, "reason": "Chest pain on exertion", "urgency": "urgent"}, format="json")
    assert ref.status_code == 201, ref.json()
    assert ClinicalAlert.objects.filter(target_user=user, title__startswith="Urgent referral").exists()
    assert [r["id"] for r in auth_client.get("/api/v1/clinical/referrals/?incoming=1").json()["results"]] == [ref.json()["id"]]
    seen = auth_client.post(f"/api/v1/clinical/referrals/{ref.json()['id']}/respond/", {"status": "seen", "response": "Stress test advised"}, format="json").json()
    assert seen["status"] == "seen" and seen["responded_at"]


# NABH COP.1.c
def test_frequent_medications_seed_an_order_set(auth_client, hospital, patient, user):
    for _ in range(3):
        Prescription.objects.create(hospital=hospital, patient=patient, doctor=user, diagnosis="URTI", medications=[{"name": "Paracetamol 650"}])
    Prescription.objects.create(hospital=hospital, patient=patient, doctor=user, diagnosis="URTI", medications=[{"name": "Cetirizine"}])
    top = auth_client.get("/api/v1/clinical/order-sets/frequent_medications/?mine=1").json()
    assert top[0]["name"].casefold() == "paracetamol 650" and top[0]["count"] == 3
    res = auth_client.post("/api/v1/clinical/order-sets/", {"name": "Fever", "kind": "medication", "items": [top[0]["example"]]}, format="json")
    assert res.status_code == 201, res.json()


# NABH COP.1.i
def test_order_sets_suggested_for_the_diagnosis(auth_client):
    auth_client.post("/api/v1/clinical/order-sets/", {"name": "Dengue workup", "kind": "lab", "diagnosis_codes": ["A97"], "diagnosis_keywords": ["dengue"],
                                                      "items": [{"name": "NS1 antigen"}, {"name": "CBC"}]}, format="json")
    auth_client.post("/api/v1/clinical/order-sets/", {"name": "Chest pain", "kind": "radiology", "diagnosis_codes": ["R07"], "items": [{"name": "Chest X-ray"}]}, format="json")
    by_icd = [s["name"] for s in auth_client.get("/api/v1/clinical/order-sets/suggest/?icd=A97.0").json()]
    by_text = [s["name"] for s in auth_client.get("/api/v1/clinical/order-sets/suggest/?diagnosis=suspected dengue").json()]
    assert "Dengue workup" in by_icd and "Dengue workup" in by_text
    assert "Chest pain" not in by_icd + by_text


# NABH COP.1.j
def test_imported_result_reviewed_with_a_comment(auth_client, patient, user):
    res = auth_client.post("/api/v1/clinical/result-reviews/", {"patient": patient.pk, "source_type": "external_report", "source_id": "EXT-77",
                                                                "summary": "Outside MRI brain", "comment": "No acute change; continue plan"}, format="json")
    assert res.status_code == 201, res.json()
    assert res.json()["reviewed_by"] == user.pk
    assert auth_client.get(f"/api/v1/clinical/result-reviews/?patient={patient.pk}").json()["results"][0]["comment"].startswith("No acute")


# NABH COP.5.c
def test_bedside_monitor_feeds_vitals_and_alerts_on_high_news2(auth_client, api_client, hospital, admission):
    device = auth_client.post("/api/v1/clinical/devices/", {"name": "Monitor B1", "kind": "monitor", "bed": admission.bed_id}, format="json").json()
    token = auth_client.post(f"/api/v1/clinical/devices/{device['id']}/rotate_token/").json()["api_token"]
    assert api_client.post("/api/v1/clinical/device-ingest/", {"heart_rate": 80}, format="json", HTTP_X_DEVICE_TOKEN="wrong").status_code == 401
    res = api_client.post("/api/v1/clinical/device-ingest/", {"heart_rate": 135, "spo2": 88, "respiratory_rate": 28, "bp_systolic": 88, "temperature_c": 39.5},
                          format="json", HTTP_X_DEVICE_TOKEN=token).json()
    assert res["risk"] == "high"
    assert ClinicalAlert.objects.filter(patient=admission.patient, alert_type="early_warning", target_department="nursing").exists()
    assert MedicalDevice.objects.get(pk=device["id"]).last_seen_at is not None


# NABH COP.10.b
def test_homecare_visit_completed_and_billed(auth_client, patient, user):
    svc = auth_client.post("/api/v1/clinical/homecare-services/", {"name": "Wound dressing", "kind": "nursing", "price": "800.00"}, format="json")
    assert svc.status_code == 201, svc.json()
    booking = auth_client.post("/api/v1/clinical/homecare-bookings/", {"patient": patient.pk, "service": svc.json()["id"], "assigned_staff": user.pk, "address": "12 MG Road, Pune",
                                                                      "scheduled_at": (timezone.now() + timedelta(hours=3)).isoformat()}, format="json")
    assert booking.status_code == 201, booking.json()
    booking = booking.json()
    done = auth_client.post(f"/api/v1/clinical/homecare-bookings/{booking['id']}/complete/", {"visit_notes": "Healing well", "vitals": {"bp": "120/80"}}, format="json").json()
    assert done["status"] == "completed" and done["bill"]
    assert auth_client.post(f"/api/v1/clinical/homecare-bookings/{booking['id']}/feedback/", {"score": 5}, format="json").json()["feedback_score"] == 5


# NABH COP.13.a
def test_customised_care_plan_with_goals_and_interventions(auth_client, patient, admission):
    plan = auth_client.post("/api/v1/clinical/care-plans/", {
        "patient": patient.pk, "admission": admission.pk, "title": "Community-acquired pneumonia", "problem": "Hypoxia",
        "goals": [{"text": "SpO2 > 94% on room air", "target_date": str(timezone.localdate() + timedelta(days=3))}],
        "interventions": [{"text": "Chest physiotherapy twice daily"}],
    }, format="json")
    assert plan.status_code == 201, plan.json()
    updated = auth_client.patch(f"/api/v1/clinical/care-plans/{plan.json()['id']}/", {"goals": [{"text": "SpO2 > 94% on room air", "status": "met"}]}, format="json").json()
    assert updated["goals"][0]["status"] == "met"
