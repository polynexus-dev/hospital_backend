import datetime
import uuid

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User, assign_role
from apps.appointments.consult_metrics import consultation_report, estimated_wait_minutes, recent_consult_minutes
from apps.appointments.models import Appointment, Doctor, Slot
from apps.patients.models import Patient
from apps.queue_mgmt.models import ServicePoint
from apps.queue_mgmt.views import board_for


@pytest.fixture
def doctor(hospital, department):
    return Doctor.objects.create(hospital=hospital, department=department, name="Mehta", default_consultation_minutes=15)


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Sunita", mobile="9988776655")


_minute = 0


def make_consult(hospital, doctor, patient, *, start, minutes, wait=10, status=Appointment.Status.COMPLETED, token=None):
    """One appointment whose consult began at `start` and lasted `minutes`."""
    global _minute
    _minute += 1
    slot = Slot.objects.create(
        hospital=hospital, doctor=doctor, date=timezone.localdate(start),
        start_time=datetime.time(6 + _minute // 60 % 12, _minute % 60), end_time=datetime.time(6 + _minute // 60 % 12, _minute % 60, 30),
    )
    return Appointment.objects.create(
        hospital=hospital, patient=patient, doctor=doctor, slot=slot, status=status, queue_token=token, registration_token=uuid.uuid4().hex,
        checked_in_at=start - datetime.timedelta(minutes=wait) if wait is not None else None,
        consult_started_at=start,
        completed_at=start + datetime.timedelta(minutes=minutes) if status == Appointment.Status.COMPLETED else None,
    )


@pytest.mark.django_db
def test_report_per_doctor_average_median_extremes_and_short_flags(hospital, doctor, patient):
    day = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0) - datetime.timedelta(days=1)
    # Back-to-back consults of 2, 8, 10, 12 minutes; one 500-minute one is a forgotten "Complete" click.
    t = day
    for m in (2, 8, 10, 12):
        make_consult(hospital, doctor, patient, start=t, minutes=m)
        t += datetime.timedelta(minutes=m)
    make_consult(hospital, doctor, patient, start=day + datetime.timedelta(hours=3), minutes=500)

    d0 = timezone.localdate(day)
    report = consultation_report(hospital.pk, d0, d0)
    [row] = report["doctors"]
    assert row["consultations"] == 4
    assert row["average_minutes"] == 8.0
    assert row["median_minutes"] == 9.0
    assert row["shortest_minutes"] == 2.0
    assert row["longest_minutes"] == 12.0
    assert row["excluded"] == 1  # implausible duration left out, but counted
    assert row["patients_per_hour"] == 7.5  # 4 patients over a 32-minute session
    assert row["average_wait_minutes"] == 10.0
    assert row["short_consultations"] == 1 and row["short_percent"] == 25.0
    assert [s["minutes"] for s in report["short_consultations"]] == [2.0]
    assert report["overall"]["excluded"] == 1

    # A stricter threshold flags more.
    assert consultation_report(hospital.pk, d0, d0, short_under=9)["overall"]["short_consultations"] == 2


@pytest.mark.django_db
def test_report_filters_by_department_and_date(hospital, department, doctor, patient):
    other = Doctor.objects.create(hospital=hospital, name="Rao")  # no department
    day = timezone.now() - datetime.timedelta(days=2)
    make_consult(hospital, doctor, patient, start=day, minutes=6)
    make_consult(hospital, other, patient, start=day, minutes=6)
    d0 = timezone.localdate(day)
    assert [r["doctor"] for r in consultation_report(hospital.pk, d0, d0, department=department.pk)["doctors"]] == ["Mehta"]
    assert consultation_report(hospital.pk, d0 + datetime.timedelta(days=1), d0 + datetime.timedelta(days=1))["doctors"] == []


@pytest.mark.django_db
def test_consultation_time_api_for_managers_and_xlsx(auth_client, hospital, doctor, patient):
    day = timezone.now() - datetime.timedelta(days=1)
    make_consult(hospital, doctor, patient, start=day, minutes=7)
    res = auth_client.get("/api/v1/consultation-time/", {"start": str(timezone.localdate(day)), "end": str(timezone.localdate())})
    assert res.status_code == 200
    assert res.data["doctors"][0]["average_minutes"] == 7.0

    xl = auth_client.get("/api/v1/consultation-time/", {"output": "xlsx"})
    assert xl.status_code == 200
    assert xl["Content-Type"].startswith("application/vnd.openxmlformats")

    assert auth_client.get("/api/v1/consultation-time/", {"start": "yesterday"}).status_code == 400


@pytest.mark.django_db
def test_doctor_without_analytics_sees_only_own_figures(hospital, department, doctor, patient):
    doc_user = User.objects.create_user(email="dr.mehta@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(doc_user, Role.objects.create(hospital=hospital, department=department, name="Doctor", template=Role.Template.DOCTOR))
    doctor.user = doc_user
    doctor.save()
    other = Doctor.objects.create(hospital=hospital, department=department, name="Rao")
    day = timezone.now() - datetime.timedelta(days=1)
    make_consult(hospital, doctor, patient, start=day, minutes=7)
    make_consult(hospital, other, patient, start=day, minutes=9)

    client = APIClient()
    client.force_authenticate(doc_user)
    res = client.get("/api/v1/consultation-time/", {"doctor": other.pk})
    assert res.status_code == 200
    assert [r["doctor"] for r in res.data["doctors"]] == ["Mehta"]


@pytest.mark.django_db
def test_staff_without_analytics_or_doctor_profile_is_refused(restricted_client):
    assert restricted_client.get("/api/v1/consultation-time/").status_code == 403


@pytest.mark.django_db
def test_queue_estimate_uses_real_recent_median_once_there_is_history(hospital, doctor, patient):
    # Too little history → configured 15 min.
    assert recent_consult_minutes(doctor) == (15.0, False)

    base = timezone.now() - datetime.timedelta(days=3)
    for i in range(12):
        make_consult(hospital, doctor, patient, start=base + datetime.timedelta(minutes=10 * i), minutes=6)
    assert recent_consult_minutes(doctor) == (6.0, True)
    assert estimated_wait_minutes(doctor, 3) == 18

    # Today: one patient 2 minutes into a consult, two waiting.
    now = timezone.now()
    serving = make_consult(hospital, doctor, patient, start=now - datetime.timedelta(minutes=2), minutes=0, status=Appointment.Status.IN_CONSULT, token=1)
    for tok in (2, 3):
        a = make_consult(hospital, doctor, patient, start=now, minutes=0, status=Appointment.Status.CHECKED_IN, token=tok)
        a.consult_started_at = None
        a.save(update_fields=["consult_started_at"])
    assert estimated_wait_minutes(doctor, 2, serving) == 16  # 2 × 6 + 4 left of the current one

    point = ServicePoint.objects.create(hospital=hospital, name="Dr Mehta OPD", kind=ServicePoint.Kind.CONSULTATION, doctor=doctor, token_prefix="M")
    board = board_for(point)
    assert board["waiting_count"] == 2
    assert board["estimated_minutes"] == 16
