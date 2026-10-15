"""MedNet M2.22 bill lock / unfreeze: a locked (final) bill refuses changes
but still takes payments of the balance; locking releases a hold; only
billing.unlock_bill reopens it. Also: a held bill blocks bed transfers."""
from datetime import date
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User, assign_role
from apps.appointments.models import Doctor
from apps.billing.adjustments import BillOnHold
from apps.billing.models import Bill, BillHoldEvent, BillItem
from apps.billing.services import recalculate_bill
from apps.facilities.models import Bed, Room, Ward
from apps.ipd.services import admit_patient, approve_ward_transfer, request_ward_transfer
from apps.patients.models import Patient

B = "/api/v1/billing/bills/"


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Kiran", last_name="Joshi", mobile="+919000001001", date_of_birth=date(1979, 9, 9))


@pytest.fixture
def bill(hospital, patient):
    b = Bill.objects.create(hospital=hospital, patient=patient, status=Bill.Status.UNPAID)
    BillItem.objects.create(bill=b, description="Consultation", quantity=1, unit_price=Decimal("1000"), total_price=0)
    recalculate_bill(b)
    return b


def client_for(hospital, department, template, email):
    role = Role.objects.create(hospital=hospital, department=department, name=email, template=template)
    u = User.objects.create_user(email=email, password="x", hospital=hospital, department=department)
    assign_role(u, role)
    c = APIClient()
    c.force_authenticate(u)
    return c


def test_locked_bill_refuses_changes_but_takes_the_balance(auth_client, bill):
    r = auth_client.post(f"{B}{bill.pk}/lock/", {"note": "Final bill"}, format="json")
    assert r.status_code == 200 and r.data["is_locked"] is True and r.data["locked_by_name"]
    add = auth_client.post(f"{B}{bill.pk}/add-item/", {"description": "Dressing", "quantity": 1, "unit_price": "200"}, format="json")
    assert add.status_code == 400 and "locked" in str(add.data)
    assert auth_client.post(f"{B}{bill.pk}/credit-note/", {"amount": "100", "reason": "x"}, format="json").status_code == 400
    pay = auth_client.post("/api/v1/billing/payments/", {"bill": bill.pk, "amount": "1000", "payment_method": "cash"}, format="json")
    assert pay.status_code == 201  # the balance can still be collected
    assert auth_client.post(f"{B}{bill.pk}/hold/", {"reason": "x"}, format="json").status_code == 400  # unlock first


def test_locking_releases_a_hold(auth_client, bill, user):
    auth_client.post(f"{B}{bill.pk}/hold/", {"reason": "Insurer query"}, format="json")
    r = auth_client.post(f"{B}{bill.pk}/lock/", {}, format="json")
    assert r.status_code == 200 and r.data["on_hold"] is False and r.data["is_locked"] is True
    actions = list(BillHoldEvent.objects.filter(bill=bill).order_by("pk").values_list("action", flat=True))
    assert actions == ["hold", "release", "lock"]
    history = auth_client.get(f"{B}{bill.pk}/hold-history/").data
    assert history[1]["reason"] == "Released automatically: bill locked"


def test_unlock_needs_the_permission_and_a_reason(auth_client, hospital, department, bill):
    auth_client.post(f"{B}{bill.pk}/lock/", {}, format="json")
    clerk = client_for(hospital, department, Role.Template.BILLING_EXECUTIVE, "clerk.lock@test-hospital.example")
    assert User.objects.get(email="clerk.lock@test-hospital.example").has_perm("billing.lock_bill")
    assert clerk.post(f"{B}{bill.pk}/unlock/", {"reason": "typo"}, format="json").status_code == 403
    assert auth_client.post(f"{B}{bill.pk}/unlock/", {}, format="json").status_code == 400
    r = auth_client.post(f"{B}{bill.pk}/unlock/", {"reason": "Insurer revised the approval"}, format="json")
    assert r.status_code == 200 and r.data["is_locked"] is False
    assert auth_client.post(f"{B}{bill.pk}/add-item/", {"description": "Dressing", "quantity": 1, "unit_price": "200"}, format="json").status_code in (200, 201)


@pytest.fixture
def ipd(hospital, department, patient):
    ward = Ward.objects.create(hospital=hospital, name="Ward A")
    room = Room.objects.create(hospital=hospital, ward=ward, room_number="101")
    a, b = (Bed.objects.create(hospital=hospital, room=room, bed_number=n) for n in ("A", "B"))
    doctor = Doctor.objects.create(hospital=hospital, department=department, name="Mehta")
    return admit_patient(hospital=hospital, patient=patient, admitting_doctor=doctor, bed=a), b


def test_running_bill_of_an_admitted_patient_cant_be_locked(auth_client, hospital, ipd):
    admission, _ = ipd
    running = Bill.objects.create(hospital=hospital, patient=admission.patient, admission=admission, status="unpaid")
    r = auth_client.post(f"{B}{running.pk}/lock/", {}, format="json")
    assert r.status_code == 400 and "still admitted" in str(r.data)


def test_held_bill_blocks_bed_transfer(hospital, ipd):
    admission, other = ipd
    held = Bill.objects.create(hospital=hospital, patient=admission.patient, admission=admission, status="unpaid",
                               on_hold=True, hold_reason="Deposit pending", held_at=timezone.now())
    with pytest.raises(BillOnHold):
        request_ward_transfer(admission=admission, to_bed=other, reason="Needs monitoring", requested_by=None)
    Bill.objects.filter(pk=held.pk).update(on_hold=False)
    transfer = request_ward_transfer(admission=admission, to_bed=other, reason="Needs monitoring", requested_by=None)
    Bill.objects.filter(pk=held.pk).update(on_hold=True)  # put on hold between request and approval
    with pytest.raises(BillOnHold):
        approve_ward_transfer(transfer, approved_by=None)
    Bill.objects.filter(pk=held.pk).update(on_hold=False)
    approve_ward_transfer(transfer, approved_by=None)
    admission.refresh_from_db()
    assert admission.bed_id == other.pk
