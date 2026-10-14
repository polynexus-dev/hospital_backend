"""Reception & billing parity with MedNet M2.22: bill hold, re-pricing on a category change,
credit notes, inactive insurers, camp report, QR document upload."""
import io
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient

from apps.billing.models import Bill, BillItem, PatientAdvance, Payment
from apps.billing.services import recalculate_bill
from apps.finance.models import JournalEntry, ServiceTariff
from apps.patients.models import Document, MobileUploadSession, Patient

B = "/api/v1/billing/bills"


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Ravi", last_name="Kale", mobile="9811100301")


@pytest.fixture
def tariffs(hospital):
    mk = lambda code, rate, rates, gst=0: ServiceTariff.objects.create(hospital=hospital, code=code, name=code.title(), rate=rate, category_rates=rates, gst_rate=gst)  # noqa: E731
    return {"room": mk("room", 2000, {"insurance": 3000}), "xray": mk("xray", 800, {"insurance": 1000}, 18), "pharm": mk("kit", 500, {})}


def bill_with(hospital, patient, tariffs, *, status=Bill.Status.UNPAID, paid=0):
    bill = Bill.objects.create(hospital=hospital, patient=patient, status=status)
    for key, qty in (("room", 2), ("xray", 1), ("pharm", 1)):
        t = tariffs[key]
        BillItem.objects.create(bill=bill, description=t.name, quantity=qty, unit_price=t.rate, total_price=0, tariff=t, gst_rate=t.gst_rate)
    BillItem.objects.create(bill=bill, description="Special nursing (manual)", quantity=1, unit_price=700, total_price=0)
    recalculate_bill(bill)  # 4000 + 800 + 144 GST + 500 + 700 = 6144
    if paid:
        Payment.objects.create(hospital=hospital, bill=bill, amount=paid, payment_method="cash")
        recalculate_bill(bill)
    return bill


# --- hold ---------------------------------------------------------------------------

def test_hold_blocks_charges_payments_links_discharge_and_release_reopens(auth_client, hospital, patient, tariffs):
    bill = bill_with(hospital, patient, tariffs)
    assert auth_client.post(f"{B}/{bill.pk}/hold/", {}, format="json").status_code == 400  # reason required
    held = auth_client.post(f"{B}/{bill.pk}/hold/", {"reason": "Insurer query on room category"}, format="json").data
    assert held["on_hold"] and held["hold_reason"].startswith("Insurer")

    refused = [
        auth_client.post(f"{B}/{bill.pk}/add-item/", {"description": "Dressing", "unit_price": 100}, format="json"),
        auth_client.post("/api/v1/billing/payments/", {"bill": bill.pk, "amount": "100", "payment_method": "cash"}, format="json"),
        auth_client.patch(f"{B}/{bill.pk}/", {"discount_amount": "50"}, format="json"),
        auth_client.post(f"{B}/{bill.pk}/change-category/", {"category": "insurance"}, format="json"),
        auth_client.post(f"{B}/{bill.pk}/credit-note/", {"reason": "x", "amount": "100"}, format="json"),
    ]
    assert [r.status_code for r in refused] == [400] * 5
    assert "on hold" in str(refused[0].data)

    from apps.billing.online_payments import GatewayError, create_payment_link

    bill.refresh_from_db()
    with pytest.raises(GatewayError, match="on hold"):
        create_payment_link(bill)

    assert auth_client.post(f"{B}/{bill.pk}/release/", {}, format="json").status_code == 400  # note required
    released = auth_client.post(f"{B}/{bill.pk}/release/", {"note": "Insurer confirmed private room"}, format="json").data
    assert not released["on_hold"]
    assert auth_client.post(f"{B}/{bill.pk}/add-item/", {"description": "Dressing", "unit_price": 100}, format="json").status_code == 201
    history = auth_client.get(f"{B}/{bill.pk}/hold-history/").data
    assert [h["action"] for h in history] == ["release", "hold"]


def test_hold_stops_a_normal_discharge(auth_client, hospital, patient):
    from apps.billing.adjustments import BillOnHold, hold
    from apps.facilities.models import Bed, Room, Ward
    from apps.ipd.models import Admission, DischargeSummary
    from apps.ipd.services import discharge_patient
    from apps.appointments.models import Doctor

    ward = Ward.objects.create(hospital=hospital, name="W", ward_type="general")
    bed = Bed.objects.create(hospital=hospital, room=Room.objects.create(hospital=hospital, ward=ward, room_number="1"), bed_number="1")
    adm = Admission.objects.create(hospital=hospital, patient=patient, admitting_doctor=Doctor.objects.create(hospital=hospital, name="D"), bed=bed)
    DischargeSummary.objects.create(hospital=hospital, admission=adm)
    bill = Bill.objects.create(hospital=hospital, patient=patient, admission=adm, status="unpaid")
    hold(bill, None, "Pending TPA approval")
    with pytest.raises(BillOnHold):
        discharge_patient(adm)
    assert discharge_patient(adm, status=Admission.Status.DAMA).status == "dama"  # leaving against advice is never held back


def test_only_hold_permission_holders_can_hold(hospital, patient, tariffs, restricted_user):
    bill = bill_with(hospital, patient, tariffs)
    c = APIClient()
    c.force_authenticate(user=restricted_user)
    assert c.post(f"{B}/{bill.pk}/hold/", {"reason": "x"}, format="json").status_code == 403


# --- category change ---------------------------------------------------------------

def test_category_change_reprices_tariff_lines_with_preview(auth_client, hospital, patient, tariffs):
    bill = bill_with(hospital, patient, tariffs)
    assert Decimal(auth_client.get(f"{B}/{bill.pk}/").data["net_amount"]) == Decimal("6144.00")
    assert auth_client.patch(f"{B}/{bill.pk}/", {"patient_category": "insurance"}, format="json").status_code == 400  # must re-price

    preview = auth_client.post(f"{B}/{bill.pk}/change-category/", {"category": "insurance", "preview": True}, format="json").data
    assert not preview["applied"] and {ln["description"] for ln in preview["lines"]} == {"Room", "Xray"}
    assert preview["net_after"] == Decimal("8380.00")  # room +2000, x-ray +200 +36 GST
    bill.refresh_from_db()
    assert bill.patient_category == "general"

    done = auth_client.post(f"{B}/{bill.pk}/change-category/", {"category": "insurance"}, format="json").data
    assert done["applied"] and done["net_after"] == Decimal("8380.00")
    bill.refresh_from_db()
    assert bill.patient_category == "insurance"
    assert BillItem.objects.get(bill=bill, description="Special nursing (manual)").unit_price == Decimal("700")  # manual price kept

    paid = bill_with(hospital, patient, tariffs, paid=6144)
    assert auth_client.post(f"{B}/{paid.pk}/change-category/", {"category": "insurance"}, format="json").status_code == 400


# --- credit notes -------------------------------------------------------------------

def test_credit_note_spread_across_services_reduces_the_due(auth_client, hospital, patient, tariffs):
    bill = bill_with(hospital, patient, tariffs, paid=2000)
    res = auth_client.post(f"{B}/{bill.pk}/credit-note/", {"reason": "Goodwill", "amount": "600"}, format="json")
    assert res.status_code == 201, res.data
    cn = res.data["credit_note"]
    assert cn["note_number"].startswith("CN") and Decimal(cn["taxable_amount"]) == Decimal("600.00")
    shares = {a["description"]: Decimal(a["amount"]) for a in cn["allocation"]}
    assert shares == {"Room": Decimal("400.00"), "Xray": Decimal("80.00"), "Kit": Decimal("50.00"), "Special nursing (manual)": Decimal("70.00")}
    assert Decimal(cn["tax_amount"]) == Decimal("14.40")  # 18% on the x-ray share
    assert Decimal(res.data["bill"]["net_amount"]) == Decimal("5529.60")
    assert res.data["bill"]["status"] == "partially_paid"
    # The sales voucher is re-posted net of the credit.
    entry = JournalEntry.objects.get(source_type="bill", source_id=str(bill.pk))
    assert {ln.account.code: (ln.debit, ln.credit) for ln in entry.lines.all()}["4000"] == (0, Decimal("5400.00"))
    assert auth_client.post(f"{B}/{bill.pk}/credit-note/", {"reason": "x", "amount": "99999"}, format="json").status_code == 400


def test_credit_note_on_a_paid_bill_refunds_or_keeps_an_advance(auth_client, hospital, patient, tariffs):
    bill = bill_with(hospital, patient, tariffs, paid=6144)
    room = BillItem.objects.get(bill=bill, description="Room")
    body = {"reason": "One day less", "items": [{"item": room.pk, "amount": "2000"}]}
    assert auth_client.post(f"{B}/{bill.pk}/credit-note/", body, format="json").status_code == 400  # must choose refund/advance
    assert auth_client.post(f"{B}/{bill.pk}/credit-note/", {**body, "settlement": "refund"}, format="json").status_code == 400  # method
    res = auth_client.post(f"{B}/{bill.pk}/credit-note/", {**body, "settlement": "refund", "refund_method": "upi", "refund_reference": "UPI-R1"}, format="json")
    assert res.status_code == 201
    assert Decimal(res.data["credit_note"]["refund_amount"]) == Decimal("2000.00") and res.data["bill"]["status"] == "paid"
    refund = Payment.objects.get(bill=bill, amount__lt=0)
    assert refund.amount == Decimal("-2000.00")
    voucher = JournalEntry.objects.get(source_type="payment", source_id=str(refund.pk))
    assert voucher.voucher_number.startswith("RFND")
    # Can't credit the room line beyond what's left of it.
    assert auth_client.post(f"{B}/{bill.pk}/credit-note/", {"reason": "x", "items": [{"item": room.pk, "amount": "2500"}], "settlement": "advance"}, format="json").status_code == 400

    res = auth_client.post(f"{B}/{bill.pk}/credit-note/", {"reason": "Kit unused", "amount": "500", "settlement": "advance"}, format="json")
    assert res.status_code == 201
    assert PatientAdvance.balance(patient) == Decimal("518.00")  # 500 spread over the services, incl. 18% GST on the x-ray share
    assert len(auth_client.get(f"{B}/{bill.pk}/credit-notes/").data) == 2
    assert auth_client.post("/api/v1/billing/payments/", {"bill": bill.pk, "amount": "-5", "payment_method": "cash"}, format="json").status_code == 400


# --- inactive insurer / TPA ----------------------------------------------------------

def test_inactive_tpa_cant_take_new_preauths_or_claims(auth_client, hospital, patient):
    from apps.tpa.models import TPACompany

    tpa = TPACompany.objects.create(hospital=hospital, name="Old TPA", code="OLD", is_active=False)
    res = auth_client.post("/api/v1/tpa/pre-auth/", {"patient": patient.pk, "tpa_company": tpa.pk, "policy_number": "P1", "claim_amount": "1000"}, format="json")
    assert res.status_code == 400 and "inactive" in str(res.data)
    pol = auth_client.post("/api/v1/finance/insurance-policies/", {"patient": patient.pk, "tpa_company": tpa.pk, "insurer": "X", "policy_number": "1"}, format="json")
    assert pol.status_code == 400
    tpa.is_active = True
    tpa.save()
    assert auth_client.post("/api/v1/finance/insurance-policies/", {"patient": patient.pk, "tpa_company": tpa.pk, "insurer": "X", "policy_number": "1"}, format="json").status_code == 201


# --- QR upload ------------------------------------------------------------------------

def _pdf():
    return SimpleUploadedFile("card.pdf", b"%PDF-1.4 test", content_type="application/pdf")


def test_qr_upload_from_a_phone(auth_client, hospital, patient):
    res = auth_client.post(f"/api/v1/patients/{patient.pk}/upload-link/", {"category": "insurance_card", "minutes": 30}, format="json")
    assert res.status_code == 201
    token, session = res.data["token"], res.data["session"]
    phone = APIClient()
    info = phone.get(f"/api/v1/mobile-upload/{token}/").data
    assert info["patient"] == "Ravi" and "Kale" not in str(info) and info["category"] == "Insurance card" and info["open"]
    up = phone.post(f"/api/v1/mobile-upload/{token}/", {"file": _pdf()}, format="multipart")
    assert up.status_code == 201 and up.data["uploads"] == 1
    doc = Document.objects.get(patient=patient)
    assert doc.category == "insurance_card" and doc.uploaded_by is not None
    bad = phone.post(f"/api/v1/mobile-upload/{token}/", {"file": SimpleUploadedFile("x.exe", b"MZ")}, format="multipart")
    assert bad.status_code == 400
    assert phone.get(f"/api/v1/mobile-upload/{token[:-2]}xx/").status_code == 404

    status = auth_client.get(f"/api/v1/patients/{patient.pk}/upload-link/{session}/").data
    assert status["uploads"] == 1
    auth_client.post(f"/api/v1/patients/{patient.pk}/upload-link/{session}/")  # close
    assert phone.post(f"/api/v1/mobile-upload/{token}/", {"file": _pdf()}, format="multipart").status_code == 410
    MobileUploadSession.objects.filter(pk=session).update(closed_at=None, expires_at=timezone.now())
    assert phone.post(f"/api/v1/mobile-upload/{token}/", {"file": _pdf()}, format="multipart").status_code == 410  # expired


def test_upload_link_needs_document_permission(hospital, patient, restricted_user):
    c = APIClient()
    c.force_authenticate(user=restricted_user)
    assert c.post(f"/api/v1/patients/{patient.pk}/upload-link/", {}, format="json").status_code in (403, 404)


# --- camp report ----------------------------------------------------------------------

def test_camp_report_counts_registrations_and_follow_on_revenue(auth_client, hospital, patient):
    import datetime

    from apps.appointments.models import Appointment, Doctor, OPDVisit, Slot
    from apps.packages.models import Campaign

    camp = Campaign.objects.create(hospital=hospital, name="Diabetes camp", start_date=timezone.localdate(), actual_spend=1000)
    doc = Doctor.objects.create(hospital=hospital, name="Rao")
    slot = Slot.objects.create(hospital=hospital, doctor=doc, date=timezone.localdate(), start_time=datetime.time(9), end_time=datetime.time(9, 15))
    appt = Appointment.objects.create(hospital=hospital, patient=patient, doctor=doc, slot=slot)
    visit_bill = Bill.objects.create(hospital=hospital, patient=patient, status="paid", net_amount=300)
    OPDVisit.objects.create(hospital=hospital, patient=patient, doctor=doc, appointment=appt, visit_type="new", campaign=camp, bill=visit_bill, payer_type="self")
    later = Bill.objects.create(hospital=hospital, patient=patient, status="unpaid")
    Bill.objects.filter(pk=later.pk).update(net_amount=5000, created_at=timezone.now() + datetime.timedelta(days=10))
    r = auth_client.get(f"/api/v1/packages/campaigns/{camp.pk}/report/").data
    assert (r["registrations"], r["patients"], r["camp_revenue"], r["follow_up_opd_revenue"], r["total_revenue"]) == (1, 1, Decimal("300.00"), Decimal("5000.00"), Decimal("5300.00"))
    assert r["return_on_spend"] == 5.3
