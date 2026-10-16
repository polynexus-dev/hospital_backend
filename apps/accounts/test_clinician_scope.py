"""Doctors manage their own work, not the doctor list, and see their own patients."""
import datetime
import secrets

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User, assign_role
from apps.appointments.models import Appointment, Doctor, Slot
from apps.patients.models import Patient


def login_as(hospital, email, template, name):
    role = Role.objects.create(hospital=hospital, name=name, template=template)
    user = User.objects.create_user(email=email, password="Pass#12345x", hospital=hospital)
    assign_role(user, role)
    client = APIClient()
    client.force_authenticate(user)
    return user, role, client


def book(hospital, doctor, patient, minute):
    slot = Slot.objects.create(hospital=hospital, doctor=doctor, date=timezone.localdate(), start_time=datetime.time(10, minute), end_time=datetime.time(10, minute + 5))
    return Appointment.objects.create(hospital=hospital, doctor=doctor, patient=patient, slot=slot, registration_token=secrets.token_urlsafe(12))


@pytest.mark.parametrize("template", ["doctor", "surgeon", "anaesthetist"])
def test_new_clinician_roles_see_only_their_own_records(hospital, template):
    assert Role.objects.create(hospital=hospital, name=f"R {template}", template=template).data_scope == "assigned_only"


def test_admin_roles_still_see_everything(hospital):
    assert Role.objects.create(hospital=hospital, name="Front", template="front_desk").data_scope == "all"


def test_doctor_cannot_create_or_edit_doctor_profiles(hospital):
    _, _, client = login_as(hospital, "doc@test-hospital.example", "doctor", "Consultant")
    assert client.post("/api/v1/doctors/", {"name": "Dr. New", "speciality": "X"}, format="json").status_code == 403
    other = Doctor.objects.create(hospital=hospital, name="Dr. Other")
    assert client.patch(f"/api/v1/doctors/{other.pk}/", {"name": "Hacked"}, format="json").status_code == 403


def test_front_desk_books_appointments_but_cannot_create_doctors(hospital):
    _, _, client = login_as(hospital, "desk@test-hospital.example", "front_desk", "Front desk")
    assert client.post("/api/v1/doctors/", {"name": "Dr. New", "speciality": "X"}, format="json").status_code == 403
    assert client.get("/api/v1/appointments/").status_code == 200


def test_doctor_sees_own_appointments_and_patients_only(hospital):
    me, _, client = login_as(hospital, "mine@test-hospital.example", "doctor", "Consultant")
    my_profile = Doctor.objects.create(hospital=hospital, name="Dr. Me", user=me)
    colleague = Doctor.objects.create(hospital=hospital, name="Dr. Colleague")
    mine = Patient.objects.create(hospital=hospital, first_name="Mine", mobile="9000000001")
    theirs = Patient.objects.create(hospital=hospital, first_name="Theirs", mobile="9000000002")
    book(hospital, my_profile, mine, 0)
    book(hospital, my_profile, mine, 10)  # two appointments: still one patient
    book(hospital, colleague, theirs, 20)

    assert client.get("/api/v1/appointments/").json()["count"] == 2
    assert [p["id"] for p in client.get("/api/v1/patients/").json()["results"]] == [mine.pk]
    assert client.get(f"/api/v1/patients/{theirs.pk}/").status_code == 404
    # break-glass: an emergency reason opens the record and is logged for review
    res = client.get(f"/api/v1/patients/{theirs.pk}/", HTTP_X_EMERGENCY_REASON="Unconscious patient in ED")
    assert res.status_code == 200


def test_doctor_works_on_own_calendar_only(hospital):
    from apps.appointments.models import Waitlist

    me, _, client = login_as(hospital, "cal@test-hospital.example", "doctor", "Consultant")
    my_profile = Doctor.objects.create(hospital=hospital, name="Dr. Me", user=me)
    colleague = Doctor.objects.create(hospital=hospital, name="Dr. Colleague")
    patient = Patient.objects.create(hospital=hospital, first_name="P", mobile="9000000003")
    book(hospital, my_profile, patient, 0)
    book(hospital, colleague, patient, 10)
    Waitlist.objects.create(hospital=hospital, patient=patient, doctor=my_profile)
    Waitlist.objects.create(hospital=hospital, patient=patient, doctor=colleague)

    assert client.get("/api/v1/slots/").json()["count"] == 1
    assert client.get("/api/v1/waitlist/").json()["count"] == 1
    assert client.get(f"/api/v1/doctors/{my_profile.pk}/queue/").status_code == 200
    assert client.get(f"/api/v1/doctors/{colleague.pk}/queue/").status_code == 403
    leave = {"start_date": str(timezone.localdate()), "end_date": str(timezone.localdate()), "reason": "x"}
    assert client.post(f"/api/v1/doctors/{colleague.pk}/block-leave/", leave, format="json").status_code == 403

    me_payload = client.get("/api/v1/users/me/").json()
    assert me_payload["data_scope"] == "assigned_only" and me_payload["own_doctor_id"] == my_profile.pk
