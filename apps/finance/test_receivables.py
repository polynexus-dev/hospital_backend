"""Finance parity with MedNet M2.22: day book, maker-checker vouchers, customer invoices, receipts, ledger, ageing, recurring invoices."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User, assign_role
from apps.finance.models import AccountsSettings, Customer, CustomerInvoice, JournalEntry, RecurringInvoice
from apps.finance.receivables import add_months, run_recurring

TODAY = timezone.localdate()
F = "/api/v1/finance"


@pytest.fixture
def checker(hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Accounts head", template=Role.Template.ADMIN)
    u = User.objects.create_user(email="checker@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(u, role)
    c = APIClient()
    c.force_authenticate(user=u)
    return c


@pytest.fixture
def setup(hospital):
    AccountsSettings.objects.create(hospital=hospital, gstin="27ABCDE1234F1Z5", state_code="27", invoice_prefix="INV")
    local = Customer.objects.create(hospital=hospital, name="Tata Motors", gstin="27AAACT2727Q1ZW", state_code="27", email="ap@tata.example", credit_days=30)
    other = Customer.objects.create(hospital=hospital, name="Infosys", state_code="29", credit_days=45)
    return local, other


def lines(*rows):
    return [{"description": d, "quantity": q, "rate": r, "gst_rate": g, "hsn_sac": "999311"} for d, q, r, g in rows]


def new_invoice(c, customer, rows, **extra):
    return c.post(f"{F}/customer-invoices/", {"customer": customer.pk, "lines": rows, **extra}, format="json")


def journal(source_type, source_id, vt):
    e = JournalEntry.objects.get(source_type=source_type, source_id=str(source_id), voucher_type=vt)
    return {ln.account.code: (ln.debit, ln.credit) for ln in e.lines.all()}


# --- invoices ----------------------------------------------------------------------

def test_invoice_gst_split_issue_and_posting(auth_client, setup):
    local, other = setup
    res = new_invoice(auth_client, local, lines(("Annual health check (50 staff)", 50, 1200, 0), ("Ambulance standby", 2, 5000, 18)))
    assert res.status_code == 201, res.data
    inv = res.data
    assert (inv["status"], inv["invoice_number"], Decimal(inv["taxable_amount"])) == ("draft", "", Decimal("70000.00"))
    assert (Decimal(inv["cgst"]), Decimal(inv["sgst"]), Decimal(inv["igst"]), Decimal(inv["total_amount"])) == (Decimal("900.00"), Decimal("900.00"), 0, Decimal("71800.00"))
    assert not JournalEntry.objects.filter(source_type="customer_invoice").exists()  # drafts don't post

    # Drafts are editable; issued invoices aren't.
    edited = auth_client.patch(f"{F}/customer-invoices/{inv['id']}/", {"notes": "PO 4471"}, format="json")
    assert edited.status_code == 200 and edited.data["notes"] == "PO 4471" and len(edited.data["lines"]) == 2
    issued = auth_client.post(f"{F}/customer-invoices/{inv['id']}/issue/").data
    fy = f"{TODAY.year if TODAY.month >= 4 else TODAY.year - 1}"
    assert issued["status"] == "issued" and issued["invoice_number"].startswith(f"INV/{fy}-") and issued["invoice_number"].endswith("/0001")
    assert auth_client.patch(f"{F}/customer-invoices/{inv['id']}/", {"notes": "x"}, format="json").status_code == 400
    assert auth_client.delete(f"{F}/customer-invoices/{inv['id']}/").status_code == 400
    assert journal("customer_invoice", inv["id"], "sales") == {"1120": (Decimal("71800.00"), 0), "4000": (0, Decimal("70000.00")), "2100": (0, Decimal("1800.00"))}
    assert Decimal(issued["outstanding"]) == Decimal("71800.00") and issued["due_date"] == str(TODAY + timedelta(days=30))

    # Another state: IGST.
    inter = new_invoice(auth_client, other, lines(("Consulting", 1, 10000, 18)), issue=True).data
    assert (Decimal(inter["igst"]), Decimal(inter["cgst"]), inter["status"]) == (Decimal("1800.00"), 0, "issued")
    assert inter["invoice_number"].endswith("/0002")

    pdf = auth_client.get(f"{F}/customer-invoices/{inv['id']}/pdf/")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    auth_client.post(f"{F}/customer-invoices/{inv['id']}/email/")
    assert mail.outbox[-1].to == ["ap@tata.example"] and mail.outbox[-1].attachments[0][0].startswith("Invoice_INV-")


def test_bad_lines_are_refused(auth_client, setup):
    local, _ = setup
    assert new_invoice(auth_client, local, []).status_code == 400
    assert new_invoice(auth_client, local, lines(("x", 1, 100, 7))).status_code == 400  # not a GST slab
    assert new_invoice(auth_client, local, [{"description": "x", "rate": 100, "discount": 500}]).status_code == 400


def test_receipts_tds_status_and_cancel_rules(auth_client, setup):
    local, _ = setup
    inv = new_invoice(auth_client, local, lines(("Retainer", 1, 100000, 18)), issue=True).data
    pay = lambda **kw: auth_client.post(f"{F}/customer-receipts/", {"customer": local.pk, "invoice": inv["id"], "mode": "neft", "reference": "UTR1", **kw}, format="json")  # noqa: E731
    assert pay(amount="200000").status_code == 400  # more than due
    assert auth_client.post(f"{F}/customer-receipts/", {"customer": local.pk, "invoice": inv["id"], "amount": "100", "mode": "neft"}, format="json").status_code == 400  # no UTR
    r = pay(amount="90000", tds_amount="10000")
    assert r.status_code == 201
    assert journal("customer_receipt", r.data["id"], "receipt") == {"1010": (Decimal("90000.00"), 0), "1310": (Decimal("10000.00"), 0), "1120": (0, Decimal("100000.00"))}
    got = auth_client.get(f"{F}/customer-invoices/{inv['id']}/").data
    assert got["status"] == "partially_paid" and Decimal(got["outstanding"]) == Decimal("18000.00")
    assert auth_client.post(f"{F}/customer-invoices/{inv['id']}/cancel/", {"reason": "wrong"}, format="json").status_code == 400  # money received
    done = auth_client.post(f"{F}/customer-invoices/{inv['id']}/receive/", {"amount": "18000", "mode": "upi", "reference": "UPI77"}, format="json")
    assert done.status_code == 200 and done.data["status"] == "paid"

    other = new_invoice(auth_client, local, lines(("Typo", 1, 500, 0)), issue=True).data
    assert auth_client.post(f"{F}/customer-invoices/{other['id']}/cancel/", {}, format="json").status_code == 400  # reason
    c = auth_client.post(f"{F}/customer-invoices/{other['id']}/cancel/", {"reason": "Raised twice"}, format="json").data
    assert c["status"] == "cancelled"
    assert journal("customer_invoice", other["id"], "credit_note") == {"1120": (0, Decimal("500.00")), "4000": (Decimal("500.00"), 0)}


def test_customer_ledger_and_ageing(auth_client, setup):
    local, other = setup
    old = new_invoice(auth_client, local, lines(("Q1 retainer", 1, 10000, 0)), invoice_date=str(TODAY - timedelta(days=100)), issue=True).data
    new = new_invoice(auth_client, local, lines(("Q2 retainer", 1, 20000, 0)), issue=True).data
    auth_client.post(f"{F}/customer-receipts/", {"customer": local.pk, "invoice": old["id"], "amount": "4000", "mode": "cash"}, format="json")
    auth_client.post(f"{F}/customer-receipts/", {"customer": local.pk, "amount": "1000", "mode": "upi", "reference": "UPI9"}, format="json")  # on account
    led = auth_client.get(f"{F}/customers/{local.pk}/ledger/", {"start": str(TODAY - timedelta(days=30)), "end": str(TODAY)}).data
    assert led["opening"] == Decimal("10000.00")  # the Q1 invoice, before the period
    assert [r["kind"] for r in led["rows"]] == ["invoice", "receipt", "receipt"]
    assert led["closing"] == Decimal("25000.00")

    age = auth_client.get(f"{F}/customers/ageing/").data
    row = next(r for r in age["rows"] if r["customer"] == local.pk)
    assert (row["over_90"], row["61_90"], row["current"], row["on_account"], row["total"]) == (0, Decimal("6000.00"), Decimal("20000.00"), Decimal("1000.00"), Decimal("25000.00"))
    assert new["id"]
    xl = auth_client.get(f"{F}/customers/ageing/", {"output": "xlsx"})
    assert xl.status_code == 200
    csv = auth_client.get(f"{F}/customers/{local.pk}/ledger/", {"output": "csv"}).content.decode()
    assert "Closing balance" in csv


# --- recurring ---------------------------------------------------------------------

def test_recurring_invoices_run_once_per_period_and_catch_up(auth_client, setup):
    local, _ = setup
    start = TODAY - timedelta(days=70)
    res = auth_client.post(f"{F}/recurring-invoices/", {"customer": local.pk, "name": "Pharmacy space rent", "frequency": "monthly",
                                                         "start_date": str(start), "lines": lines(("Space rent", 1, 25000, 18)), "auto_email": True}, format="json")
    assert res.status_code == 201, res.data
    made = run_recurring()
    assert made == 3  # start, +1 month, +2 months (catch-up)
    assert run_recurring() == 0  # idempotent
    invs = CustomerInvoice.objects.filter(recurring_id=res.data["id"]).order_by("invoice_date")
    assert [i.invoice_date for i in invs] == [start, add_months(start, 1), add_months(start, 2)]
    assert all(i.status == "issued" and i.total_amount == Decimal("29500.00") for i in invs)
    assert len(mail.outbox) == 3
    rec = RecurringInvoice.objects.get(pk=res.data["id"])
    assert rec.next_run_date == add_months(start, 3)
    bad = auth_client.post(f"{F}/recurring-invoices/", {"customer": local.pk, "name": "Bad", "start_date": str(TODAY), "lines": lines(("x", 1, 10, 3))}, format="json")
    assert bad.status_code == 400


def test_add_months_keeps_month_end():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


# --- vouchers and day book ------------------------------------------------------------

def test_manual_voucher_needs_a_second_person(auth_client, checker, hospital):
    body = {"voucher_type": "payment", "entry_date": str(TODAY), "narration": "Electricity bill Sept", "party_name": "MSEDCL",
            "lines": [{"account": "5000", "debit": "42000"}, {"account": "1010", "credit": "42000"}]}
    bad = auth_client.post(f"{F}/journal/", {**body, "lines": [{"account": "5000", "debit": "1"}, {"account": "1010", "credit": "2"}]}, format="json")
    assert bad.status_code == 400 and "equal" in str(bad.data)
    v = auth_client.post(f"{F}/journal/", body, format="json").data
    assert v["status"] == "pending" and v["voucher_number"].startswith("PV/")
    tb = lambda: {a["code"]: a for a in auth_client.get(f"{F}/trial-balance/").data["accounts"]}  # noqa: E731
    assert "5000" not in tb()  # pending: not in the books
    assert auth_client.post(f"{F}/journal/{v['id']}/approve/").status_code == 400  # maker can't approve
    rej = checker.post(f"{F}/journal/{v['id']}/reject/", {"reason": "Attach the bill"}, format="json").data
    assert rej["status"] == "rejected"
    assert checker.patch(f"{F}/journal/{v['id']}/", {"narration": "x"}, format="json").status_code == 403  # only the maker edits
    again = auth_client.patch(f"{F}/journal/{v['id']}/", {"narration": "Electricity bill Sept (bill attached)"}, format="json").data
    assert again["status"] == "pending"
    ok = checker.post(f"{F}/journal/{v['id']}/approve/").data
    assert ok["status"] == "approved" and ok["approved_by_name"] is not None
    assert tb()["5000"]["debit"] == 42000.0
    assert auth_client.delete(f"{F}/journal/{v['id']}/").status_code == 400  # posted

    s = AccountsSettings.for_hospital(hospital.pk)
    s.voucher_approval_required = False
    s.save()
    direct = auth_client.post(f"{F}/journal/", {**body, "voucher_type": "contra", "lines": [{"account": "1010", "debit": "5000"}, {"account": "1000", "credit": "5000"}]}, format="json").data
    assert direct["status"] == "approved" and direct["voucher_number"].startswith("CV/")


def test_day_book_filters_pages_and_totals(auth_client, setup):
    local, _ = setup
    for n in range(3):
        new_invoice(auth_client, local, lines((f"Service {n}", 1, 1000 * (n + 1), 0)), issue=True)
    auth_client.post(f"{F}/journal/", {"voucher_type": "journal", "lines": [{"account": "5000", "debit": "10"}, {"account": "1010", "credit": "10"}]}, format="json")
    day = auth_client.get(f"{F}/day-book/", {"page_size": 2}).data
    assert day["count"] == 3 and day["pages"] == 2 and len(day["results"]) == 2  # pending voucher excluded
    assert day["totals"]["debit"] == day["totals"]["credit"] == Decimal("6000.00")
    assert day["page_totals"]["debit"] == Decimal("3000.00")
    assert auth_client.get(f"{F}/day-book/", {"status": "all"}).data["count"] == 4
    assert auth_client.get(f"{F}/day-book/", {"status": "pending"}).data["totals"]["debit"] == Decimal("10.00")
    assert auth_client.get(f"{F}/day-book/", {"voucher_type": "receipt"}).data["count"] == 0
    assert auth_client.get(f"{F}/day-book/", {"account": "4000", "party": "tata"}).data["count"] == 3
    assert auth_client.get(f"{F}/day-book/", {"search": "INV/"}).data["count"] == 3
    csv = auth_client.get(f"{F}/day-book/", {"output": "csv"}).content.decode()
    assert "Customer receivables" in csv and csv.strip().splitlines()[-1].startswith(",,,,,Total")


def test_books_are_closed_to_staff_without_finance_access(restricted_user):
    c = APIClient()
    c.force_authenticate(user=restricted_user)
    for url in ("day-book/", "trial-balance/", "journal/", "gst/", "tally-export/", "customers/", "customer-invoices/", "accounts-settings/"):
        assert c.get(f"{F}/{url}").status_code == 403, url


def test_accounts_settings(auth_client):
    assert auth_client.put(f"{F}/accounts-settings/", {"gstin": "bad"}, format="json").status_code == 400
    res = auth_client.put(f"{F}/accounts-settings/", {"gstin": "29abcde1234f1z5", "bank_details": "HDFC 0001"}, format="json").data
    assert (res["gstin"], res["state_code"]) == ("29ABCDE1234F1Z5", "29")
