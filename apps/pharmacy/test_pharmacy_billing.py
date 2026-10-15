"""Dispensed medicines are charged to the patient's bill (MRP incl. GST),
returns are credited, and billing never blocks a dispense."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.appointments.models import Doctor
from apps.billing.adjustments import hold, release
from apps.billing.models import Bill, BillItem
from apps.facilities.models import Bed, Room, Ward
from apps.ipd.services import admit_patient
from apps.patients.models import Patient
from apps.pharmacy.models import DispenseRecord, Medicine, MedicineBatch

PH = "/api/v1/pharmacy"


@pytest.fixture
def s(hospital, department):
    med = Medicine.objects.create(hospital=hospital, name="Pantoprazole", strength="40 mg", gst_rate=12)
    batch = MedicineBatch.objects.create(hospital=hospital, medicine=med, batch_number="PZ1", expiry_date=timezone.localdate() + timedelta(days=300),
                                         quantity_available=100, mrp=Decimal("112.00"), purchase_price=Decimal("60"))
    patient = Patient.objects.create(hospital=hospital, first_name="Ravi", last_name="Kale", mobile="9811100601")
    return {"hospital": hospital, "department": department, "med": med, "batch": batch, "patient": patient}


def dispense(client, s, qty=2, **extra):
    r = client.post(f"{PH}/dispense-records/", {"batch": s["batch"].pk, "quantity": qty, "patient": s["patient"].pk, **extra}, format="json")
    assert r.status_code == 201, r.data
    return r.data


def test_opd_dispenses_go_on_one_draft_bill_for_the_day_at_mrp(auth_client, s):
    first = dispense(auth_client, s)
    assert first["bill"] and first["amount"] == Decimal("224.00") and first["billing_note"] == ""
    item = BillItem.objects.get(pk=first["bill_item"])
    assert (item.unit_price, item.gst_rate, item.hsn_sac, item.source) == (Decimal("100.00"), Decimal("12.00"), "3004", "pharmacy")
    assert item.description == "Pantoprazole 40 mg (batch PZ1)"
    second = dispense(auth_client, s, qty=1)
    assert second["bill"] == first["bill"]
    bill = Bill.objects.get(pk=first["bill"])
    assert bill.status == "draft" and bill.admission_id is None and bill.net_amount == Decimal("336.00")


def test_inpatient_dispense_goes_on_the_running_bill(auth_client, s):
    ward = Ward.objects.create(hospital=s["hospital"], name="W1")
    bed = Bed.objects.create(hospital=s["hospital"], room=Room.objects.create(hospital=s["hospital"], ward=ward, room_number="1"), bed_number="1")
    doc = Doctor.objects.create(hospital=s["hospital"], department=s["department"], name="Rao")
    adm = admit_patient(hospital=s["hospital"], patient=s["patient"], admitting_doctor=doc, bed=bed)
    d = dispense(auth_client, s)
    assert Bill.objects.get(pk=d["bill"]).admission_id == adm.pk


def test_free_issue_needs_a_reason_and_counter_sales_are_noted(auth_client, s):
    bad = auth_client.post(f"{PH}/dispense-records/", {"batch": s["batch"].pk, "quantity": 1, "patient": s["patient"].pk, "bill": False}, format="json")
    assert bad.status_code == 400
    free = dispense(auth_client, s, qty=1, bill=False, no_charge_reason="Part of the cataract package")
    assert free["bill_item"] is None and free["billing_note"].startswith("Issued without charge")
    counter = auth_client.post(f"{PH}/dispense-records/", {"batch": s["batch"].pk, "quantity": 1}, format="json").data
    assert counter["bill_item"] is None and "no patient" in counter["billing_note"]
    assert {r["id"] for r in auth_client.get(f"{PH}/dispense-records/", {"billed": "false"}).data["results"]} == {free["id"], counter["id"]}


def test_bill_on_hold_never_blocks_dispensing_and_it_can_be_billed_later(auth_client, s, user):
    first = dispense(auth_client, s)
    bill = Bill.objects.get(pk=first["bill"])
    hold(bill, user, "Address check")
    held = dispense(auth_client, s, qty=1)
    assert held["bill_item"] is None and "on hold" in held["billing_note"]
    assert auth_client.post(f"{PH}/dispense-records/{held['id']}/bill/").status_code == 400
    release(Bill.objects.get(pk=bill.pk), user, "Verified")
    billed = auth_client.post(f"{PH}/dispense-records/{held['id']}/bill/").data
    assert billed["bill"] == bill.pk and DispenseRecord.objects.get(pk=held["id"]).bill_item_id


def test_returns_are_credited_on_the_open_bill_and_capped(auth_client, s):
    d = dispense(auth_client, s, qty=3)
    r = auth_client.post(f"{PH}/returns/", {"batch": s["batch"].pk, "patient": s["patient"].pk, "quantity": 2, "reason": "Discontinued", "dispense_record": d["id"]}, format="json")
    assert r.status_code == 201 and r.data["credit_item"]
    bill = Bill.objects.get(pk=d["bill"])
    assert bill.net_amount == Decimal("112.00")  # 3 x 112 - 2 x 112
    over = auth_client.post(f"{PH}/returns/", {"batch": s["batch"].pk, "patient": s["patient"].pk, "quantity": 2, "reason": "x", "dispense_record": d["id"]}, format="json")
    assert over.status_code == 400
    Bill.objects.filter(pk=bill.pk).update(status="paid")
    late = auth_client.post(f"{PH}/returns/", {"batch": s["batch"].pk, "patient": s["patient"].pk, "quantity": 1, "reason": "x", "dispense_record": d["id"]}, format="json").data
    assert late["credit_item"] is None and "credit note" in late["billing_note"]


def test_insurer_non_payable_medicine_is_flagged_on_the_dispensed_line(auth_client, s):
    from apps.billing.services import recalculate_bill
    from apps.tpa.models import NonPayableItem, NonPayableList

    lst = NonPayableList.objects.create(hospital=s["hospital"], name="IRDAI")
    NonPayableItem.objects.create(hospital=s["hospital"], non_payable_list=lst, medicine=s["med"])
    d = dispense(auth_client, s)
    bill = Bill.objects.get(pk=d["bill"])
    Bill.objects.filter(pk=bill.pk).update(patient_category="insurance")
    bill.refresh_from_db()
    recalculate_bill(bill)
    bill.refresh_from_db()
    assert bill.non_payable_amount == Decimal("224.00") and BillItem.objects.get(pk=d["bill_item"]).non_payable
