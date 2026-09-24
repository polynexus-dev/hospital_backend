"""Registration (AAC.1), queue (AAC.2.h/i), referrals, sharing and device
integration tests."""
from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.patients.models import MobileOTP, Patient

REG = {"first_name": "Sunita", "last_name": "Patil", "date_of_birth": "1990-05-05", "mobile": "9876500001"}


def test_exact_duplicate_blocked_probable_flagged(auth_client):
    first = auth_client.post("/api/v1/patients/", REG, format="json")
    assert first.status_code == 201 and first.json()["uhid"]
    dup = auth_client.post("/api/v1/patients/", REG, format="json")
    assert dup.status_code == 409 and dup.json()["matches"][0]["match"] == "exact"
    probable = auth_client.post("/api/v1/patients/check-duplicates/", {**REG, "last_name": "Pat", "date_of_birth": "1991-01-01"}, format="json").json()
    assert probable and probable[0]["match"] == "probable"


def test_merge_moves_records(auth_client, hospital):
    from apps.clinical.models import Allergy

    a = Patient.objects.create(hospital=hospital, first_name="Raj", mobile="9000000401")
    b = Patient.objects.create(hospital=hospital, first_name="Raj", mobile="9000000402")
    Allergy.objects.create(hospital=hospital, patient=b, allergen="latex")
    res = auth_client.post(f"/api/v1/patients/{a.pk}/merge/", {"duplicate_id": b.pk}, format="json").json()
    assert res["moved"]["Allergy"] == 1
    assert not Patient.objects.filter(pk=b.pk).exists()


def test_mobile_otp_verification(auth_client, hospital, monkeypatch):
    p = Patient.objects.create(hospital=hospital, first_name="Om", mobile="9000000403")
    from apps.patients import registration

    sent = {}
    monkeypatch.setattr(registration, "issue_otp", lambda h, m, purpose="registration": sent.setdefault("code", _real_issue(h, m)))
    auth_client.post(f"/api/v1/patients/{p.pk}/verify-mobile/", {}, format="json")
    assert auth_client.post(f"/api/v1/patients/{p.pk}/verify-mobile/", {"otp": "000000" if sent["code"] != "000000" else "111111"}, format="json").status_code == 400
    assert auth_client.post(f"/api/v1/patients/{p.pk}/verify-mobile/", {"otp": sent["code"]}, format="json").json()["verified"] is True
    p.refresh_from_db()
    assert p.mobile_verified_at is not None


def _real_issue(hospital, mobile):
    from apps.patients.registration import OTP_TTL_MINUTES
    import secrets

    from django.contrib.auth.hashers import make_password

    from apps.core.encryption import blind_index, normalize_phone

    code = f"{secrets.randbelow(10**6):06d}"
    MobileOTP.objects.create(hospital=hospital, mobile_hash=blind_index(normalize_phone(mobile)), code_hash=make_password(code), expires_at=timezone.now() + timedelta(minutes=OTP_TTL_MINUTES))
    return code


def test_uhid_config_format(auth_client, hospital):
    res = auth_client.patch("/api/v1/patients/uhid-config/", {"prefix": "SWH", "branch_code": "NGP", "pattern": "{PREFIX}-{BRANCH}-{YY}{MM}-{SEQ}"}, format="json")
    assert res.status_code == 200 and "{SEQ}" not in res.json()["preview"]
    uhid = auth_client.post("/api/v1/patients/", {"first_name": "New", "mobile": "9000000404"}, format="json").json()["uhid"]
    assert uhid.startswith(f"SWH-NGP-{timezone.localdate():%y%m}-")
    assert auth_client.patch("/api/v1/patients/uhid-config/", {"pattern": "{PREFIX}"}, format="json").status_code == 400


def test_offline_sync_idempotent(auth_client):
    payload = {"patients": [{**REG, "mobile": "9000000405", "offline_client_id": "tab-1-0001"}]}
    first = auth_client.post("/api/v1/patients/offline-sync/", payload, format="json").json()["results"][0]
    assert first["status"] == "created"
    again = auth_client.post("/api/v1/patients/offline-sync/", payload, format="json").json()["results"][0]
    assert again["status"] == "already_synced" and again["id"] == first["id"]


def test_queue_tokens_call_next_priority_and_public_board(auth_client, api_client, hospital):
    point = auth_client.post("/api/v1/queue/service-points/", {"name": "Billing", "kind": "billing", "counter_label": "Counter 2", "token_prefix": "B"}, format="json").json()
    t1 = auth_client.post("/api/v1/queue/tokens/", {"service_point": point["id"], "visitor_name": "A"}, format="json").json()
    t2 = auth_client.post("/api/v1/queue/tokens/", {"service_point": point["id"], "visitor_name": "B", "priority": True}, format="json").json()
    assert t1["label"] == "B001" and t2["label"] == "B002"
    assert t1["wait"]["estimated_minutes"] >= 0
    called = auth_client.post(f"/api/v1/queue/service-points/{point['id']}/call_next/").json()
    assert called["label"] == "B002"  # priority first
    display = auth_client.post("/api/v1/queue/displays/", {"name": "Lobby TV", "service_points": [point["id"]]}, format="json").json()
    board = api_client.get(f"/api/v1/queue/public/board/{display['key']}/").json()
    assert board["boards"][0]["now_serving"]["label"] == "B002" and board["boards"][0]["waiting"] == ["B001"]
    assert "Sunita" not in str(board)


def test_consultation_start_stamps_kpi_time(auth_client, hospital):
    from apps.appointments.models import Appointment, Doctor, Slot

    doc = Doctor.objects.create(hospital=hospital, name="Dr K")
    slot = Slot.objects.create(hospital=hospital, doctor=doc, date=timezone.localdate(), start_time="10:00", end_time="10:15")
    p = Patient.objects.create(hospital=hospital, first_name="Q", mobile="9000000406")
    appt = Appointment.objects.create(hospital=hospital, patient=p, doctor=doc, slot=slot)
    auth_client.post(f"/api/v1/appointments/{appt.pk}/check-in/")
    auth_client.post(f"/api/v1/appointments/{appt.pk}/start-consult/")
    appt.refresh_from_db()
    assert appt.consult_started_at is not None
    sched = auth_client.get(f"/api/v1/doctors/{doc.pk}/schedule/").json()
    assert sched["slots"][0]["patient"] == "Q"
    assert auth_client.get(f"/api/v1/doctors/{doc.pk}/schedule/?output=pdf").content[:4] == b"%PDF"


def test_referral_alerts_target_and_response(auth_client, hospital, user):
    from apps.appointments.models import Doctor
    from apps.clinical.models import ClinicalAlert

    cardio = Doctor.objects.create(hospital=hospital, name="Dr Heart", user=user)
    p = Patient.objects.create(hospital=hospital, first_name="R", mobile="9000000407")
    ref = auth_client.post("/api/v1/clinical/referrals/", {"patient": p.pk, "to_doctor": cardio.pk, "reason": "Pre-op cardiac fitness", "urgency": "urgent"}, format="json").json()
    assert ClinicalAlert.objects.filter(target_user=user, title__contains="referral").exists()
    assert auth_client.get("/api/v1/clinical/referrals/?incoming=1").json()["results"][0]["id"] == ref["id"]
    assert auth_client.post(f"/api/v1/clinical/referrals/{ref['id']}/respond/", {"status": "seen", "response": "Fit for surgery"}, format="json").json()["status"] == "seen"


def test_record_share_within_group_only(auth_client, hospital, other_hospital, other_user):
    from rest_framework.test import APIClient

    from apps.core.models import HospitalGroup

    p = Patient.objects.create(hospital=hospital, first_name="Share", mobile="9000000408")
    base = {"patient": p.pk, "shared_with": str(other_hospital.pk), "purpose": "Blood bank cross-match", "patient_consent": True, "expires_at": (timezone.now() + timedelta(days=2)).isoformat()}
    assert auth_client.post("/api/v1/clinical/record-shares/", base, format="json").status_code == 400
    group = HospitalGroup.objects.create(name="Group")
    for h in (hospital, other_hospital):
        h.group = group
        h.save()
    res = auth_client.post("/api/v1/clinical/record-shares/", base, format="json")
    assert res.status_code == 201, res.json()
    from apps.accounts.models import Role, assign_role

    role = Role.objects.create(hospital=other_hospital, name="Doc", template=Role.Template.DOCTOR)
    assign_role(other_user, role)
    other = APIClient()
    other.force_authenticate(user=other_user)
    share_id = other.get("/api/v1/clinical/shared-with-me/").json()[0]["id"]
    assert other.get(f"/api/v1/clinical/shared-with-me/{share_id}/").json()["patient"]["uhid"] == p.uhid


def test_monitor_ingest_high_news2_alerts_nurses(auth_client, api_client, hospital):
    from apps.appointments.models import Doctor
    from apps.clinical.models import ClinicalAlert
    from apps.facilities.models import Bed, Room, Ward
    from apps.ipd.services import admit_patient

    bed = Bed.objects.create(hospital=hospital, room=Room.objects.create(hospital=hospital, ward=Ward.objects.create(hospital=hospital, name="ICU"), room_number="1"), bed_number="ICU-1")
    p = Patient.objects.create(hospital=hospital, first_name="Mon", mobile="9000000409")
    admit_patient(hospital=hospital, patient=p, admitting_doctor=Doctor.objects.create(hospital=hospital, name="Dr"), bed=bed, admission_type="emergency", department=None, admission_diagnosis="Sepsis", source_encounter=None)
    dev = auth_client.post("/api/v1/clinical/devices/", {"name": "Monitor ICU-1", "kind": "monitor", "bed": bed.pk}, format="json").json()
    token = auth_client.post(f"/api/v1/clinical/devices/{dev['id']}/rotate_token/").json()["api_token"]
    res = api_client.post("/api/v1/clinical/device-ingest/", {"heart_rate": 138, "spo2": 88, "respiratory_rate": 30, "bp_systolic": 85, "temperature_c": 39.5}, format="json", HTTP_X_DEVICE_TOKEN=token).json()
    assert res["risk"] == "high"
    assert ClinicalAlert.objects.filter(patient=p, alert_type="early_warning", target_department="nursing").exists()
