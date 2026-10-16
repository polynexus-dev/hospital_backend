"""/reports/live-dashboard/: every owner-dashboard figure from live records."""
import datetime
import secrets
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.appointments.models import Appointment, Doctor, Slot
from apps.billing.models import Bill, BillItem
from apps.core.models import Department
from apps.patients.models import Patient


@pytest.fixture
def setup(hospital, department):
    other_dept = Department.objects.create(hospital=hospital, name="Orthopaedics")
    cardio = Doctor.objects.create(hospital=hospital, department=department, name="Mehta")
    ortho = Doctor.objects.create(hospital=hospital, department=other_dept, name="Patil")
    patient = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9822000001")
    now = timezone.now()

    def visit(doctor, minute, status, wait_minutes=None):
        slot = Slot.objects.create(hospital=hospital, doctor=doctor, date=timezone.localdate(), start_time=datetime.time(9, minute), end_time=datetime.time(9, minute + 5))
        return Appointment.objects.create(
            hospital=hospital, patient=patient, doctor=doctor, slot=slot, status=status,
            registration_token=secrets.token_urlsafe(16),  # set by book_appointment in real use
            checked_in_at=now - datetime.timedelta(minutes=30) if wait_minutes is not None or status != Appointment.Status.BOOKED else None,
            consult_started_at=now - datetime.timedelta(minutes=30 - wait_minutes) if wait_minutes is not None else None,
            completed_at=now if status == Appointment.Status.COMPLETED else None,
        )

    visit(cardio, 0, Appointment.Status.COMPLETED, wait_minutes=10)
    visit(cardio, 10, Appointment.Status.IN_CONSULT, wait_minutes=20)
    visit(ortho, 20, Appointment.Status.CHECKED_IN)
    bill = Bill.objects.create(hospital=hospital, patient=patient)
    BillItem.objects.create(bill=bill, description="Consultation", quantity=1, unit_price=Decimal("500"), total_price=Decimal("500"), source="consultation", doctor=cardio)
    BillItem.objects.create(bill=bill, description="Paracetamol", quantity=2, unit_price=Decimal("20"), total_price=Decimal("40"), source="pharmacy")
    return {"cardio": cardio, "ortho": ortho, "department": department}


def get(client, **params):
    today = timezone.localdate().isoformat()
    res = client.get("/api/v1/reports/live-dashboard/", {"start": today, "end": today, **params})
    assert res.status_code == 200, res.content
    return res.json()


def test_figures_come_from_todays_records(auth_client, setup):
    d = get(auth_client)  # end=today must include today
    assert d["opd"]["completed"]["value"] == 1
    assert d["opd"]["encounters"]["value"] == 3  # checked in today
    assert d["opd"]["avg_wait_minutes"]["value"] == 15.0  # (10 + 20) / 2
    assert d["now"]["in_consultation"] == 1 and d["now"]["waiting"] == 1 and d["now"]["consults_done_today"] == 1
    revenue = {r["service"]: Decimal(r["amount"]) for r in d["revenue_by_service"]}
    assert revenue == {"Consultation": Decimal("500"), "Pharmacy": Decimal("40")}
    assert Decimal(d["doctor_billed"]) == Decimal("500")


def test_department_and_doctor_filters_really_filter(auth_client, setup):
    by_dept = get(auth_client, department=setup["department"].id)
    assert by_dept["opd"]["encounters"]["value"] == 2
    assert [r["department"] for r in by_dept["department_volume"]] == [setup["department"].name]

    by_doctor = get(auth_client, doctor=setup["ortho"].id)
    assert by_doctor["now"]["waiting"] == 1 and by_doctor["now"]["in_consultation"] == 0
    assert by_doctor["revenue_by_service"] == []


def test_trend_is_null_without_a_previous_period(auth_client, setup):
    d = get(auth_client)
    assert d["opd"]["completed"]["change_pct"] is None  # nothing yesterday to compare with
