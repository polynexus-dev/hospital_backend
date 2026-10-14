"""Revenue-share pack: medicine-cost toggle, deduction heads, statements and the consultant TDS report."""
import datetime
from decimal import Decimal
from unittest import mock

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.appointments.models import Doctor
from apps.billing.models import Bill, BillItem
from apps.billing.services import recalculate_bill
from apps.facilities.models import Bed, Room, Ward
from apps.finance.models import (
    DoctorPayout, DoctorPayoutRule, JournalEntry, PayoutDeductionHead, PayoutStatementDelivery, ServiceTariff,
)
from apps.finance.payout_statements import fy_quarter, statement_link
from apps.finance.payouts import eligible_lines, generate_statements
from apps.ipd.models import Admission
from apps.patients.models import Patient
from apps.pharmacy.models import DispenseRecord, Medicine, MedicineBatch, MedicineReturn

TODAY = timezone.localdate()
PERIOD = {"period_start": str(TODAY - datetime.timedelta(days=30)), "period_end": str(TODAY)}
API = "/api/v1/finance/doctor-payouts/"


@pytest.fixture
def s(hospital, department):
    patient = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9876500021")
    doc = Doctor.objects.create(hospital=hospital, department=department, name="Mehta", email="mehta@example.com", phone="9876500099", pan="ABCDE1234F")
    surgery = ServiceTariff.objects.create(hospital=hospital, code="SURG", name="Appendectomy", rate=30000, department="Surgery")
    room = ServiceTariff.objects.create(hospital=hospital, code="ROOM", name="Room rent", rate=10000, department="IPD")
    return {"hospital": hospital, "patient": patient, "doc": doc, "surgery": surgery, "room": room}


def admission_bill(s, *, medicine_cost=0, returned_cost=0):
    ward = Ward.objects.create(hospital=s["hospital"], name="Surgical", ward_type="general")
    bed = Bed.objects.create(hospital=s["hospital"], room=Room.objects.create(hospital=s["hospital"], ward=ward, room_number="201"), bed_number="S1")
    adm = Admission.objects.create(hospital=s["hospital"], patient=s["patient"], admitting_doctor=s["doc"], bed=bed)
    if medicine_cost:
        med = Medicine.objects.create(hospital=s["hospital"], name="Ceftriaxone 1g")
        batch = MedicineBatch.objects.create(hospital=s["hospital"], medicine=med, batch_number="B1", expiry_date=TODAY + datetime.timedelta(days=300),
                                             quantity_available=100, mrp=200, purchase_price=100)
        DispenseRecord.objects.create(hospital=s["hospital"], batch=batch, quantity=medicine_cost // 100, patient=s["patient"])
        if returned_cost:
            MedicineReturn.objects.create(hospital=s["hospital"], batch=batch, quantity=returned_cost // 100, patient=s["patient"], reason="Unused")
    bill = Bill.objects.create(hospital=s["hospital"], patient=s["patient"], admission=adm, status=Bill.Status.UNPAID)
    surg = BillItem.objects.create(bill=bill, description="Appendectomy", quantity=1, unit_price=30000, total_price=0, tariff=s["surgery"],
                                   doctor=s["doc"], service_date=TODAY)
    BillItem.objects.create(bill=bill, description="Room rent", quantity=1, unit_price=10000, total_price=0, tariff=s["room"], service_date=TODAY)
    recalculate_bill(bill)
    return bill, surg


def rule(s, **kw):
    return DoctorPayoutRule.objects.create(hospital=s["hospital"], name="Surgeon share", value=kw.pop("value", 20), doctor=s["doc"], **kw)


@pytest.mark.django_db
def test_medicine_cost_is_taken_off_before_the_share_when_the_rule_says_so(s):
    rule(s, deduct_medicine_cost=True)
    _, surg = admission_bill(s, medicine_cost=5000, returned_cost=1000)
    [(item, _r, _d, base, amount, med)] = eligible_lines(s["hospital"].pk, TODAY, TODAY)
    # Net medicine cost 4,000 is shared over the bill's 40,000: the surgery line (30,000) carries 3,000.
    assert (item.pk, med, base, amount) == (surg.pk, Decimal("3000.00"), Decimal("27000.00"), Decimal("5400.00"))
    [payout] = generate_statements(s["hospital"].pk, TODAY, TODAY)
    line = payout.lines.get()
    assert line.medicine_cost == Decimal("3000.00") and "net of medicine cost" in line.rule_label


@pytest.mark.django_db
def test_medicine_cost_is_ignored_by_default_and_for_fixed_rules(s):
    rule(s)
    admission_bill(s, medicine_cost=5000)
    [(_i, _r, _d, base, amount, med)] = eligible_lines(s["hospital"].pk, TODAY, TODAY)
    assert (base, amount, med) == (Decimal("30000.00"), Decimal("6000.00"), Decimal("0"))
    DoctorPayoutRule.objects.update(basis="fixed", value=2500, deduct_medicine_cost=True)
    [(_i, _r, _d, _b, amount, med)] = eligible_lines(s["hospital"].pk, TODAY, TODAY)
    assert (amount, med) == (Decimal("2500.00"), Decimal("0"))


@pytest.mark.django_db
def test_deduction_heads_reduce_net_and_post_to_their_ledger(auth_client, s):
    rule(s)
    admission_bill(s)
    head = auth_client.post("/api/v1/finance/payout-deduction-heads/", {"name": "Advance recovery", "default_amount": "500"}, format="json")
    assert head.status_code == 201, head.data
    assert auth_client.post("/api/v1/finance/payout-deduction-heads/", {"name": "Bad", "ledger_code": "9999"}, format="json").status_code == 400
    PayoutDeductionHead.objects.create(hospital=s["hospital"], name="Indemnity", default_amount=200, auto_apply=True)

    [stmt] = auth_client.post(f"{API}generate/", {**PERIOD, "tds_percent": "10"}, format="json").data
    assert Decimal(stmt["deductions_total"]) == Decimal("200.00")  # auto-applied head
    pid = stmt["id"]
    res = auth_client.post(f"{API}{pid}/add-deduction/", {"head": head.data["id"], "remarks": "Aug advance"}, format="json")
    assert res.status_code == 200
    # Gross 6,000 - TDS 600 - deductions 700 = 4,700
    assert (Decimal(res.data["deductions_total"]), Decimal(res.data["net_payable"])) == (Decimal("700.00"), Decimal("4700.00"))
    assert auth_client.post(f"{API}{pid}/add-deduction/", {"head": head.data["id"], "amount": "99999"}, format="json").status_code == 400

    auto = next(d for d in res.data["deductions"] if d["head_name"] == "Indemnity")
    res = auth_client.post(f"{API}{pid}/remove-deduction/", {"deduction": auto["id"]}, format="json")
    assert Decimal(res.data["net_payable"]) == Decimal("4900.00")
    res = auth_client.post(f"{API}{pid}/add-deduction/", {"head": head.data["id"], "amount": "100"}, format="json")
    assert Decimal(auth_client.post(f"{API}{pid}/remove-deduction/", {"head": head.data["id"]}, format="json").data["deductions_total"]) == Decimal("500.00")
    res = auth_client.post(f"{API}{pid}/add-deduction/", {"head": head.data["id"], "amount": "100"}, format="json")

    auth_client.post(f"{API}{pid}/approve/")
    assert auth_client.post(f"{API}{pid}/add-deduction/", {"head": head.data["id"]}, format="json").status_code == 400  # not draft
    auth_client.post(f"{API}{pid}/mark-paid/", {"payment_mode": "neft", "payment_reference": "UTR9"}, format="json")
    entry = JournalEntry.objects.get(source_type="doctor_payout", source_id=str(pid))
    lines = {ln.account.code: (ln.debit, ln.credit) for ln in entry.lines.all()}
    assert lines == {"5100": (Decimal("6000.00"), 0), "1010": (0, Decimal("4800.00")), "2110": (0, Decimal("600.00")), "4200": (0, Decimal("600.00"))}


@pytest.mark.django_db
def test_statements_are_sent_by_email_and_whatsapp_and_logged(auth_client, s):
    rule(s)
    admission_bill(s)
    [stmt] = auth_client.post(f"{API}generate/", PERIOD, format="json").data
    pid = stmt["id"]
    pdf = auth_client.get(f"{API}{pid}/statement/")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert auth_client.post(f"{API}{pid}/send/", {"channels": ["email"]}, format="json").status_code == 400  # draft
    auth_client.post(f"{API}{pid}/approve/")

    with mock.patch("apps.communications.adapters.StubWhatsAppProvider.send", return_value="wa-1") as wa:
        res = auth_client.post(f"{API}{pid}/send/", {"channels": ["email", "whatsapp"]}, format="json")
    assert res.status_code == 200 and [d["sent"] for d in res.data] == [True, True]
    assert mail.outbox[-1].to == ["mehta@example.com"] and mail.outbox[-1].attachments[0][2] == "application/pdf"
    body = wa.call_args.kwargs["body"]
    assert "net Rs. 5,400.00" in body and "/payout-statement/" in body

    # The WhatsApp link opens the PDF without signing in; a tampered one doesn't.
    link = statement_link(DoctorPayout.objects.get(pk=pid)).split("/api/v1", 1)[1]
    anon = APIClient()
    assert anon.get(f"/api/v1{link}").content.startswith(b"%PDF")
    assert anon.get(f"/api/v1{link[:-3]}xx/").status_code == 404

    s["doc"].email = ""
    s["doc"].save()
    res = auth_client.post(f"{API}send-statements/", {**PERIOD, "channels": "email"}, format="json")
    assert res.data == {"statements": 1, "sent": 0, "failed": 1}
    assert PayoutStatementDelivery.objects.filter(payout_id=pid).count() == 3
    log = auth_client.get(f"{API}{pid}/deliveries/").data
    assert log[0]["error"].startswith("No email address")
    assert auth_client.get(f"{API}{pid}/").data["last_sent_at"]


@pytest.mark.django_db
def test_consultant_tds_report_views_and_exports(auth_client, s):
    rule(s)
    admission_bill(s)
    [stmt] = auth_client.post(f"{API}generate/", {**PERIOD, "tds_percent": "10"}, format="json").data
    auth_client.post(f"{API}{stmt['id']}/approve/")
    params = {"from": PERIOD["period_start"], "to": PERIOD["period_end"]}
    assert auth_client.get(f"{API}tds-report/", params).data["rows"] == []  # approved, not yet paid
    assert len(auth_client.get(f"{API}tds-report/", {**params, "basis": "period"}).data["rows"]) == 1
    auth_client.post(f"{API}{stmt['id']}/mark-paid/", {"payment_mode": "neft", "payment_reference": "UTR7"}, format="json")

    summary = auth_client.get(f"{API}tds-report/", params).data
    assert summary["rows"][0][1] == fy_quarter(TODAY) and summary["totals"]["tds_amount"] == Decimal("600.00")
    consultant = auth_client.get(f"{API}tds-report/", {**params, "view": "consultant"}).data
    assert consultant["rows"] == [["Dr. Mehta", "ABCDE1234F", 1, Decimal("6000.00"), Decimal("600.00"), Decimal("0.00"), Decimal("5400.00")]]
    detail = auth_client.get(f"{API}tds-report/", {**params, "view": "detail"}).data
    assert detail["rows"][0][5] == "UTR7"
    xl = auth_client.get(f"{API}tds-report/", {**params, "view": "consultant", "output": "xlsx"})
    assert xl.status_code == 200 and xl["Content-Type"].startswith("application/vnd.openxmlformats")
    csv = auth_client.get(f"{API}tds-report/", {**params, "view": "detail", "output": "csv"}).content.decode()
    assert "ABCDE1234F" in csv and csv.strip().splitlines()[-1].startswith("Total")


def test_fy_quarter():
    assert fy_quarter(datetime.date(2026, 5, 1)) == "FY2026-27 Q1"
    assert fy_quarter(datetime.date(2027, 2, 1)) == "FY2026-27 Q4"
    assert fy_quarter(datetime.date(2026, 12, 31)) == "FY2026-27 Q3"


@pytest.mark.django_db
def test_doctor_pan_is_validated_and_stored_encrypted(auth_client, s):
    res = auth_client.patch(f"/api/v1/doctors/{s['doc'].pk}/", {"pan": "abcde1234f"}, format="json")
    assert res.status_code == 200 and res.data["pan"] == "ABCDE1234F"
    assert auth_client.patch(f"/api/v1/doctors/{s['doc'].pk}/", {"pan": "12345"}, format="json").status_code == 400
    from django.db import connection

    with connection.cursor() as c:
        c.execute("SELECT pan FROM appointments_doctor WHERE id = %s", [s["doc"].pk])
        assert c.fetchone()[0] != "ABCDE1234F"


@pytest.mark.django_db
def test_staff_without_finance_access_cannot_use_the_pack(restricted_client):
    assert restricted_client.get("/api/v1/finance/payout-deduction-heads/").status_code == 403
    assert restricted_client.get(f"{API}tds-report/", {"from": "2026-01-01", "to": "2026-01-31"}).status_code == 403
    assert restricted_client.post(f"{API}send-statements/", {"channels": "email"}, format="json").status_code == 403
