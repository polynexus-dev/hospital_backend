from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.appointments.models import Doctor, Slot
from apps.patients.models import Patient


@pytest.fixture
def family(hospital):
    a = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9811100001")
    b = Patient.objects.create(hospital=hospital, first_name="Kiran", mobile="9811100002", alternate_mobile="9811100001")
    return a, b


def _portal_client(hospital, mobile, monkeypatch):
    from apps.patients import registration

    codes = {}
    real = registration.issue_otp

    def capture(h, m, purpose="registration"):
        codes["otp"] = real(h, m, purpose)
        return codes["otp"]

    monkeypatch.setattr(registration, "issue_otp", capture)
    c = APIClient()
    assert c.post("/api/v1/portal/auth/request-otp/", {"hospital": hospital.slug, "mobile": mobile}, format="json").json()["sent"]
    res = c.post("/api/v1/portal/auth/verify-otp/", {"hospital": hospital.slug, "mobile": mobile, "otp": codes["otp"]}, format="json").json()
    c.credentials(HTTP_AUTHORIZATION=f"Portal {res['token']}")
    return c, res


def test_portal_login_covers_family_and_blocks_staff_api(hospital, family, monkeypatch):
    c, res = _portal_client(hospital, "9811100001", monkeypatch)
    assert {p["name"] for p in res["patients"]} == {"Asha", "Kiran"}
    assert c.get("/api/v1/portal/me/").status_code == 200
    assert c.get("/api/v1/patients/").status_code == 401  # portal token is useless on staff endpoints


def test_unknown_number_gets_same_response_but_no_otp(hospital):
    from apps.patients.models import MobileOTP

    c = APIClient()
    assert c.post("/api/v1/portal/auth/request-otp/", {"hospital": hospital.slug, "mobile": "9000099999"}, format="json").json() == {"sent": True}
    assert not MobileOTP.objects.exists()


def test_portal_booking_and_reports_visibility(hospital, family, monkeypatch):
    from apps.laboratory.models import LabOrder

    a, b = family
    doc = Doctor.objects.create(hospital=hospital, name="Dr Portal")
    slot = Slot.objects.create(hospital=hospital, doctor=doc, date=timezone.localdate() + timedelta(days=1), start_time="09:00", end_time="09:15")
    c, _ = _portal_client(hospital, "9811100001", monkeypatch)
    assert c.get(f"/api/v1/portal/doctors/{doc.pk}/slots/?date={slot.date}").json()[0]["id"] == slot.pk
    booked = c.post("/api/v1/portal/appointments/", {"slot": slot.pk, "patient": b.pk}, format="json")
    assert booked.status_code == 201
    assert c.post("/api/v1/portal/appointments/", {"slot": slot.pk, "patient": a.pk}, format="json").status_code == 409
    LabOrder.objects.create(hospital=hospital, patient=a, status="resulted")
    verified = LabOrder.objects.create(hospital=hospital, patient=a, status="verified")
    labs = c.get(f"/api/v1/portal/reports/?patient={a.pk}").json()["laboratory"]
    assert [l["id"] for l in labs] == [verified.pk]
    assert c.get(f"/api/v1/portal/reports/lab/{verified.pk}/pdf/").content[:4] == b"%PDF"


def test_portal_cannot_see_other_patients(hospital, family, monkeypatch):
    stranger = Patient.objects.create(hospital=hospital, first_name="X", mobile="9000011111")
    c, _ = _portal_client(hospital, "9811100001", monkeypatch)
    assert c.get(f"/api/v1/portal/prescriptions/?patient={stranger.pk}").status_code == 404


def test_measures_and_complaint(hospital, family, monkeypatch):
    c, _ = _portal_client(hospital, "9811100001", monkeypatch)
    assert c.post("/api/v1/portal/measures/", {"instrument": "prem_opd", "answers": {"q1": 5}}, format="json").status_code == 400
    res = c.post("/api/v1/portal/measures/", {"instrument": "prem_opd", "answers": {"q1": 5, "q2": 3, "q3": 4, "q4": 4}}, format="json").json()
    assert float(res["score"]) == 4.0
    assert c.post("/api/v1/portal/complaints/", {"description": "Long wait at billing"}, format="json").status_code == 201


def test_teleconsult_flow(auth_client, hospital, family):
    a, _ = family
    doc = Doctor.objects.create(hospital=hospital, name="Dr Video")
    tc = auth_client.post("/api/v1/telemedicine/consultations/", {"patient": a.pk, "doctor": doc.pk, "scheduled_at": (timezone.now() + timedelta(minutes=10)).isoformat()}, format="json").json()
    token = tc["patient_join_link"].rsplit("/", 1)[1]
    public = APIClient()
    assert public.get(f"/api/v1/telemedicine/join/{token}/").json()["doctor"] == "Dr Video"
    assert public.post(f"/api/v1/telemedicine/join/{token}/", {}, format="json").status_code == 400  # consent needed
    joined = public.post(f"/api/v1/telemedicine/join/{token}/", {"consent": True}, format="json").json()
    assert joined["join_url"].startswith("https://meet.jit.si/hc-")
    doc_join = auth_client.post(f"/api/v1/telemedicine/consultations/{tc['id']}/doctor_join/").json()
    assert doc_join["join_url"] == joined["join_url"] and doc_join["status"] == "in_progress"
    done = auth_client.post(f"/api/v1/telemedicine/consultations/{tc['id']}/complete/", {"clinical_notes": "URTI, advised rest"}, format="json").json()
    assert done["status"] == "completed"
    from apps.clinical.models import ConsentRecord

    assert ConsentRecord.objects.filter(patient=a, consent_type="telemedicine").exists()
    assert public.get(f"/api/v1/telemedicine/join/{token}x/").status_code == 404
