import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.appointments.models import Doctor
from apps.billing.models import Bill, BillItem, Payment
from apps.finance.models import DoctorPayout, DoctorPayoutLine, DoctorPayoutRule, JournalEntry, ServiceTariff
from apps.finance.payouts import eligible_lines, generate_statements
from apps.patients.models import Patient

TODAY = timezone.localdate()
PERIOD = {"period_start": str(TODAY - datetime.timedelta(days=30)), "period_end": str(TODAY)}


@pytest.fixture
def s(hospital, department):
    patient = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9876500011")
    mehta = Doctor.objects.create(hospital=hospital, department=department, name="Mehta")
    rao = Doctor.objects.create(hospital=hospital, department=department, name="Rao")
    consult = ServiceTariff.objects.create(hospital=hospital, code="CONS", name="Consultation", rate=1000, department="OPD")
    echo = ServiceTariff.objects.create(hospital=hospital, code="ECHO", name="2D Echo", rate=2500, department="Cardiology")
    return {"hospital": hospital, "department": department, "patient": patient, "mehta": mehta, "rao": rao, "consult": consult, "echo": echo}


def bill_with(s, items, *, status=Bill.Status.UNPAID, discount=0, category="general", bill_items=None):
    bill = Bill.objects.create(hospital=s["hospital"], patient=s["patient"], status=status, discount_amount=discount, patient_category=category)
    made = []
    for doctor, tariff, qty, source in items:
        made.append(BillItem.objects.create(bill=bill, description=tariff.name, quantity=qty, unit_price=tariff.rate, total_price=0,
                                            tariff=tariff, doctor=doctor, source=source, service_date=TODAY))
    from apps.billing.services import recalculate_bill

    recalculate_bill(bill)
    return bill, made


def rule(s, name, value, **kw):
    return DoctorPayoutRule.objects.create(hospital=s["hospital"], name=name, value=value, **kw)


@pytest.mark.django_db
def test_most_specific_rule_wins_and_percent_is_on_net_after_discount(s):
    rule(s, "Department default", 20, doctor_department=s["department"])
    rule(s, "Echo fixed", 400, tariff=s["echo"], basis="fixed")
    rule(s, "Mehta personal", 30, doctor=s["mehta"])
    _, (c_mehta, c_rao, echo_rao) = bill_with(s, [(s["mehta"], s["consult"], 1, ""), (s["rao"], s["consult"], 1, ""), (s["rao"], s["echo"], 2, "")], discount=700)
    rows = {item.pk: (r.name, base, amount) for item, r, _d, base, amount in eligible_lines(s["hospital"].pk, TODAY, TODAY)}
    # Bill total 7000, discount 700 → each line keeps 90% of its value.
    assert rows[c_mehta.pk] == ("Mehta personal", Decimal("900.00"), Decimal("270.00"))
    assert rows[c_rao.pk] == ("Department default", Decimal("900.00"), Decimal("180.00"))
    assert rows[echo_rao.pk] == ("Echo fixed", Decimal("4500.00"), Decimal("800.00"))


@pytest.mark.django_db
def test_system_lines_draft_bills_and_unmatched_items_are_not_paid(s):
    rule(s, "Everyone", 10)
    bill_with(s, [(s["mehta"], s["consult"], 1, "bed_charge"), (s["mehta"], s["consult"], 1, "interim")])
    bill_with(s, [(s["mehta"], s["consult"], 1, "")], status=Bill.Status.DRAFT)
    bill_with(s, [(None, s["consult"], 1, "")])
    assert eligible_lines(s["hospital"].pk, TODAY, TODAY) == []


@pytest.mark.django_db
def test_collected_rule_waits_for_full_payment_and_dates_by_last_payment(s):
    rule(s, "On collection", 50, earned_on="collected")
    bill, _ = bill_with(s, [(s["mehta"], s["consult"], 1, "")])
    assert eligible_lines(s["hospital"].pk, TODAY, TODAY) == []
    Payment.objects.create(hospital=s["hospital"], bill=bill, amount=Decimal("1000"))
    from apps.billing.services import recalculate_bill

    recalculate_bill(bill)
    [(_, _, earned, _, amount)] = eligible_lines(s["hospital"].pk, TODAY, TODAY)
    assert earned == TODAY and amount == Decimal("500.00")


@pytest.mark.django_db
def test_statements_apply_tds_never_pay_a_line_twice_and_cancel_frees_lines(s):
    rule(s, "Everyone", 20)
    bill_with(s, [(s["mehta"], s["consult"], 1, ""), (s["mehta"], s["echo"], 1, ""), (s["rao"], s["consult"], 1, "")])
    start, end = TODAY - datetime.timedelta(days=1), TODAY
    mehta, rao = generate_statements(s["hospital"].pk, start, end)
    assert (mehta.doctor, mehta.gross_payout, mehta.tds_amount, mehta.net_payable) == (s["mehta"], Decimal("700.00"), Decimal("70.00"), Decimal("630.00"))
    assert rao.gross_payout == Decimal("200.00")

    # Re-running replaces drafts rather than duplicating them.
    generate_statements(s["hospital"].pk, start, end)
    assert DoctorPayout.objects.filter(status="draft").count() == 2
    assert DoctorPayoutLine.objects.count() == 3

    # Approved statements keep their lines; a new run finds nothing more to pay.
    DoctorPayout.objects.update(status="approved")
    assert generate_statements(s["hospital"].pk, start, end) == []

    # Cancelling releases the lines for the next run.
    DoctorPayout.objects.filter(doctor=s["rao"]).update(status="draft")
    rao_stmt = DoctorPayout.objects.get(doctor=s["rao"])
    rao_stmt.lines.all().delete()
    rao_stmt.status = "cancelled"
    rao_stmt.save()
    assert [p.doctor for p in generate_statements(s["hospital"].pk, start, end)] == [s["rao"]]


@pytest.mark.django_db
def test_payout_api_generate_approve_pay_posts_journal(auth_client, s):
    rule(s, "Everyone", 20)
    bill_with(s, [(s["mehta"], s["consult"], 2, "")])
    preview = auth_client.get("/api/v1/finance/doctor-payouts/preview/", PERIOD)
    assert preview.status_code == 200 and preview.data["doctors"][0]["gross_payout"] == Decimal("400.00")

    res = auth_client.post("/api/v1/finance/doctor-payouts/generate/", {**PERIOD, "tds_percent": "10"}, format="json")
    assert res.status_code == 201
    pid = res.data[0]["id"]
    assert auth_client.post(f"/api/v1/finance/doctor-payouts/{pid}/mark-paid/", {"payment_mode": "neft", "payment_reference": "UTR1"}, format="json").status_code == 400
    assert auth_client.post(f"/api/v1/finance/doctor-payouts/{pid}/approve/").status_code == 200
    assert auth_client.post(f"/api/v1/finance/doctor-payouts/{pid}/mark-paid/", {"payment_mode": "neft"}, format="json").status_code == 400
    paid = auth_client.post(f"/api/v1/finance/doctor-payouts/{pid}/mark-paid/", {"payment_mode": "neft", "payment_reference": "UTR1"}, format="json")
    assert paid.status_code == 200 and paid.data["status"] == "paid"
    assert auth_client.post(f"/api/v1/finance/doctor-payouts/{pid}/cancel/").status_code == 400

    entry = JournalEntry.objects.get(source_type="doctor_payout", source_id=str(pid))
    lines = {ln.account.code: (ln.debit, ln.credit) for ln in entry.lines.all()}
    assert lines == {"5100": (Decimal("400.00"), 0), "1010": (0, Decimal("360.00")), "2110": (0, Decimal("40.00"))}

    xl = auth_client.get(f"/api/v1/finance/doctor-payouts/{pid}/lines/", {"output": "xlsx"})
    assert xl.status_code == 200 and xl["Content-Type"].startswith("application/vnd.openxmlformats")
    assert auth_client.post("/api/v1/finance/doctor-payouts/", {}, format="json").status_code == 405


@pytest.mark.django_db
def test_staff_without_finance_access_cannot_see_payouts(restricted_client):
    assert restricted_client.get("/api/v1/finance/doctor-payouts/").status_code == 403
    assert restricted_client.get("/api/v1/finance/doctor-payout-rules/").status_code == 403
