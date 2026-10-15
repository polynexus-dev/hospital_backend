"""MedNet M2.22 bill block, wider reach: while one of the patient's open bills
is on hold, new lab / imaging orders are refused (STAT excepted), and every
screen can ask whether the patient has a held bill."""
from datetime import date

import pytest
from django.utils import timezone

from apps.billing.models import Bill
from apps.laboratory.models import LabOrder, LabTest
from apps.patients.models import Patient
from apps.radiology.models import RadiologyProcedure


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Asha", last_name="Patil", gender="female", date_of_birth=date(1980, 1, 1), mobile="+919000000501")


@pytest.fixture
def held_bill(hospital, patient, user):
    return Bill.objects.create(hospital=hospital, patient=patient, status=Bill.Status.UNPAID, on_hold=True, hold_reason="Pending insurer query", held_by=user, held_at=timezone.now())


@pytest.fixture
def cbc(hospital):
    return LabTest.objects.create(hospital=hospital, name="CBC", code="CBC", price=300)


def test_hold_status_lists_held_open_bills_only(auth_client, patient, held_bill):
    r = auth_client.get(f"/api/v1/billing/hold-status/?patient={patient.pk}")
    assert r.status_code == 200 and r.data["on_hold"] is True
    assert r.data["bills"][0]["bill_number"] == held_bill.bill_number and r.data["bills"][0]["reason"] == "Pending insurer query"
    Bill.objects.filter(pk=held_bill.pk).update(status=Bill.Status.PAID)  # a settled bill no longer blocks anything
    assert auth_client.get(f"/api/v1/billing/hold-status/?patient={patient.pk}").data["on_hold"] is False


def test_hold_status_is_tenant_scoped(api_client, other_user, patient, held_bill):
    api_client.force_authenticate(other_user)
    assert api_client.get(f"/api/v1/billing/hold-status/?patient={patient.pk}").data == {"on_hold": False, "bills": []}


def test_lab_order_refused_while_bill_on_hold_but_stat_goes_through(auth_client, patient, held_bill, cbc):
    r = auth_client.post("/api/v1/laboratory/orders/", {"patient": patient.pk, "ordered_tests": [cbc.pk]}, format="json")
    assert r.status_code == 400 and r.data["code"] == "bill_on_hold" and held_bill.bill_number in r.data["detail"]
    assert not LabOrder.objects.filter(patient=patient).exists()
    stat = auth_client.post("/api/v1/laboratory/orders/", {"patient": patient.pk, "ordered_tests": [cbc.pk], "priority": "stat"}, format="json")
    assert stat.status_code == 201


def test_lab_order_allowed_once_released(auth_client, patient, held_bill, cbc):
    Bill.objects.filter(pk=held_bill.pk).update(on_hold=False)
    assert auth_client.post("/api/v1/laboratory/orders/", {"patient": patient.pk, "ordered_tests": [cbc.pk]}, format="json").status_code == 201


def test_radiology_order_refused_while_bill_on_hold(auth_client, hospital, patient, held_bill):
    xray = RadiologyProcedure.objects.create(hospital=hospital, name="Chest X-ray", modality="xray", price=500)
    r = auth_client.post("/api/v1/radiology/orders/", {"patient": patient.pk, "procedure": xray.pk}, format="json")
    assert r.status_code == 400 and r.data["code"] == "bill_on_hold"
