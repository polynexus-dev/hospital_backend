from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.billing.models import Bill
from apps.patients.models import Patient


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Vikas", mobile="9000000601")


def test_bill_quantity_bug_fixed_and_tariff_gst(auth_client, hospital, patient):
    bill = auth_client.post("/api/v1/billing/bills/", {"patient": patient.pk, "status": "unpaid"}, format="json").json()
    r = auth_client.post(f"/api/v1/billing/bills/{bill['id']}/add-item/", {"description": "Syringe", "quantity": 2, "unit_price": "500"}, format="json").json()
    assert Decimal(r["total_price"]) == Decimal("1000.00")
    tariff = auth_client.post("/api/v1/finance/tariff/", {"code": "PH-STENT", "name": "Coronary stent", "rate": "20000", "category_rates": {"insurance": 25000}, "hsn_sac": "9021", "gst_rate": "12"}, format="json").json()
    Bill.objects.filter(pk=bill["id"]).update(patient_category="insurance")
    item = auth_client.post(f"/api/v1/billing/bills/{bill['id']}/add-item/", {"tariff": tariff["id"]}, format="json").json()
    assert Decimal(item["unit_price"]) == Decimal("25000") and Decimal(item["tax_amount"]) == Decimal("3000.00")
    b = auth_client.get(f"/api/v1/billing/bills/{bill['id']}/").json()
    assert Decimal(b["net_amount"]) == Decimal("29000.00") and b["bill_number"].startswith("INV")


def test_journals_balance_tally_and_gst(auth_client, hospital, patient):
    from apps.finance.models import JournalEntry

    bill = auth_client.post("/api/v1/billing/bills/", {"patient": patient.pk, "status": "unpaid"}, format="json").json()
    tariff = auth_client.post("/api/v1/finance/tariff/", {"code": "MED1", "name": "Medicine kit", "rate": "1000", "hsn_sac": "3004", "gst_rate": "12"}, format="json").json()
    auth_client.post(f"/api/v1/billing/bills/{bill['id']}/add-item/", {"tariff": tariff["id"]}, format="json")
    auth_client.post(f"/api/v1/billing/bills/{bill['id']}/add-item/", {"description": "Consultation", "unit_price": "500", "hsn_sac": "9993"}, format="json")
    auth_client.post("/api/v1/billing/payments/", {"bill": bill["id"], "amount": "1620", "payment_method": "upi"}, format="json")
    assert JournalEntry.objects.filter(hospital=hospital, voucher_type="sales").exists()
    assert JournalEntry.objects.filter(hospital=hospital, voucher_type="receipt").exists()
    tb = auth_client.get("/api/v1/finance/trial-balance/").json()
    assert tb["total_debit"] == tb["total_credit"] > 0
    xml = auth_client.get("/api/v1/finance/tally-export/").content.decode()
    assert "<VOUCHER VCHTYPE=\"Sales\"" in xml and "<VOUCHER VCHTYPE=\"Receipt\"" in xml
    assert "<LEDGER NAME=\"Output GST\"" in auth_client.get("/api/v1/finance/tally-export/?masters=1").content.decode()
    gst = auth_client.get("/api/v1/finance/gst/").json()
    by_rate = {r["rate"]: r for r in gst["b2cs"]}
    assert by_rate[12.0]["total_tax"] == 120.0 and by_rate[0.0]["type"] == "exempt"
    assert auth_client.get("/api/v1/finance/gst/?export=xlsx").content[:2] == b"PK"
    statement = auth_client.get(f"/api/v1/finance/patients/{patient.pk}/statement/").json()
    assert statement["outstanding"] == 0.0


def test_vendor_invoice_three_way_match_payment_and_aging(auth_client, hospital):
    from apps.inventory.models import Item, ItemCategory, PurchaseOrder, POItem

    vendor = auth_client.post("/api/v1/finance/vendors/", {"name": "MedSupply Pvt Ltd", "gstin": "27ABCDE1234F1Z5", "credit_days": 30, "email": "ap@medsupply.example"}, format="json").json()
    cat = ItemCategory.objects.create(hospital=hospital, name="Consumables", code="CON")
    item = Item.objects.create(hospital=hospital, category=cat, name="Gloves", code="GLV")
    po = PurchaseOrder.objects.create(hospital=hospital, po_number="PO-1", vendor_name=vendor["name"], status="submitted")
    poi = POItem.objects.create(purchase_order=po, item=item, ordered_quantity=100, unit_cost=10)
    grn = auth_client.post("/api/v1/inventory/grns/", {"purchase_order": po.pk, "items": [{"po_item": poi.pk, "received_quantity": 90, "rejected_quantity": 5, "batch_number": "G1"}]}, format="json").json()
    assert grn["has_discrepancy"] is True and "short 10" in grn["discrepancy_notes"] and "5 rejected" in grn["discrepancy_notes"]
    inv = auth_client.post("/api/v1/finance/vendor-invoices/", {"vendor": vendor["id"], "purchase_order": po.pk, "invoice_number": "MS/001", "taxable_amount": "1000", "gst_amount": "120"}, format="json").json()
    assert inv["status"] == "mismatch"  # invoiced 1000, accepted only 85 × 10
    assert auth_client.post(f"/api/v1/finance/vendor-invoices/{inv['id']}/pay/", {}, format="json").status_code == 400
    assert auth_client.post(f"/api/v1/finance/vendor-invoices/{inv['id']}/approve/", {}, format="json").status_code == 400
    assert auth_client.post(f"/api/v1/finance/vendor-invoices/{inv['id']}/approve/", {"override_reason": "Balance to follow"}, format="json").json()["status"] == "approved"
    paid = auth_client.post(f"/api/v1/finance/vendor-invoices/{inv['id']}/pay/", {"amount": "500", "reference": "UTR123"}, format="json").json()
    assert paid["status"] == "partially_paid" and Decimal(paid["outstanding"]) == Decimal("620.00")
    aging = auth_client.get("/api/v1/finance/vendor-invoices/aging/").json()
    assert aging[0]["vendor"] == "MedSupply Pvt Ltd" and aging[0]["total"] == 620.0
    note = auth_client.post("/api/v1/finance/supplier-notes/", {"vendor": vendor["id"], "invoice": inv["id"], "kind": "debit", "amount": "50", "reason": "5 rejected gloves"}, format="json").json()
    assert note["note_number"].startswith("DN")


def test_interim_bill_and_claim_settlement(auth_client, hospital, patient, monkeypatch):
    from apps.appointments.models import Doctor
    from apps.facilities.models import Bed, Room, Ward
    from apps.ipd.services import admit_patient
    from apps.tpa.models import Claim, TPACompany

    bed = Bed.objects.create(hospital=hospital, room=Room.objects.create(hospital=hospital, ward=Ward.objects.create(hospital=hospital, name="W"), room_number="1"), bed_number="1")
    adm = admit_patient(hospital=hospital, patient=patient, admitting_doctor=Doctor.objects.create(hospital=hospital, name="Dr"), bed=bed, admission_type="planned", department=None, admission_diagnosis="", source_encounter=None)
    run = auth_client.post("/api/v1/billing/bills/", {"patient": patient.pk, "admission": adm.pk, "status": "unpaid"}, format="json").json()
    auth_client.post(f"/api/v1/billing/bills/{run['id']}/add-item/", {"description": "Room rent x2", "quantity": 2, "unit_price": "3000"}, format="json")
    auth_client.post("/api/v1/billing/payments/", {"bill": run["id"], "amount": "2000", "payment_method": "cash"}, format="json")
    interim = auth_client.post("/api/v1/billing/bills/interim/", {"admission": adm.pk}, format="json").json()
    assert interim["is_interim"] is True and interim["balance_due"] == 4000.0

    sent = []
    monkeypatch.setattr("apps.clinical.notify.notify_patient", lambda p, purpose, ctx, **kw: sent.append((purpose, ctx)))
    tpa = TPACompany.objects.create(hospital=hospital, name="Medi Assist")
    claim = Claim.objects.create(hospital=hospital, patient=patient, tpa_company=tpa, claim_number="CLM1", billed_amount=6000)
    claim.status = "under_review" if "under_review" in dict(Claim.Status.choices) else list(dict(Claim.Status.choices))[1]
    claim.save()
    assert sent and sent[0][0] == "claim_status"
    s = auth_client.post("/api/v1/finance/claim-settlements/", {"claim": claim.pk, "utr_number": "UTR9", "amount_received": "5400", "tds_deducted": "600"}, format="json").json()
    assert s["is_reconciled"] is True
    assert auth_client.get("/api/v1/finance/tpa-dashboard/").json()["settled"] == 6000.0
