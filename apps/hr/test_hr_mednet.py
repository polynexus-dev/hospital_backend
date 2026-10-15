"""HR parity with MedNet M2.22: leave balances and rules (quota, accrual,
carry forward, no negative), employee dependents linked to patient records
with the staff rate category, and ESI including overtime."""
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User, assign_role
from apps.billing.models import Bill, BillItem
from apps.finance.models import ServiceTariff
from apps.hr.leave import accrued, balance, carry_forward
from apps.hr.models import Employee, EmployeeDependent, ExitRequest, LeaveBalance, LeaveRequest, LeaveType, OvertimeEntry, Payslip, SalaryStructure
from apps.hr.staff_rate import staff_link
from apps.patients.models import Patient

HR = "/api/v1/hr"


@pytest.fixture
def nurse(hospital):
    u = User.objects.create_user(email="nurse.hr@x.example", password="x", hospital=hospital, first_name="Neha", last_name="Joshi")
    return Employee.objects.create(hospital=hospital, user=u, employee_code="N100", designation="Staff Nurse", date_of_joining=date(2020, 4, 1))


@pytest.fixture
def types(hospital):
    mk = lambda code, **kw: LeaveType.objects.create(hospital=hospital, code=code, name=kw.pop("name", code), **kw)  # noqa: E731
    return {
        "cl": mk("CL", name="Casual leave", annual_quota=12, accrual="monthly"),
        "el": mk("EL", name="Earned leave", annual_quota=18, carry_forward_max=30),
        "sl": mk("SL", name="Sick leave", annual_quota=7, when_short="warn"),
        "lwp": mk("LWP", name="Leave without pay", annual_quota=0, when_short="warn", is_paid=False),
    }


def apply(client, emp, lt, start, end=None, **kw):
    return client.post(f"{HR}/leave-requests/", {"employee": emp.pk, "leave_type_master": lt.pk, "start_date": str(start), "end_date": str(end or start), **kw}, format="json")


# --- HR-1 leave balances & rules ---------------------------------------------------

def test_accrual_yearly_monthly_and_pro_rated_for_joiners(hospital, nurse, types):
    assert accrued(nurse, types["el"], 2026, as_of=date(2026, 3, 10)) == 18
    assert accrued(nurse, types["cl"], 2026, as_of=date(2026, 3, 10)) == 3  # 3 months started
    assert accrued(nurse, types["cl"], 2025, as_of=date(2026, 3, 10)) == 12  # past year: all of it
    joiner = Employee.objects.create(hospital=hospital, employee_code="N101", designation="Nurse", date_of_joining=date(2026, 7, 15))
    assert accrued(joiner, types["el"], 2026, as_of=date(2026, 8, 1)) == 9  # Jul-Dec = 6/12 of 18
    assert accrued(joiner, types["sl"], 2026, as_of=date(2026, 8, 1)) == Decimal("3.5")  # 7 x 6/12, to the half day
    assert accrued(joiner, types["el"], 2025, as_of=date(2026, 8, 1)) == 0


def test_block_type_refuses_more_than_the_balance_including_pending(auth_client, nurse, types):
    with mock.patch("apps.hr.leave.timezone.localdate", return_value=date(2026, 3, 10)):
        ok = apply(auth_client, nurse, types["cl"], date(2026, 3, 16), date(2026, 3, 17))  # 2 of 3 accrued
        assert ok.status_code == 201 and Decimal(ok.data["days"]) == 2 and not ok.data["short_balance"]
        short = apply(auth_client, nurse, types["cl"], date(2026, 3, 23), date(2026, 3, 24))  # 1 left after the pending 2
        assert short.status_code == 400 and "1" in str(short.data["detail"])
        half = apply(auth_client, nurse, types["cl"], date(2026, 3, 25), half_day=True)
        assert half.status_code == 201 and Decimal(half.data["days"]) == Decimal("0.5")
    assert LeaveRequest.objects.count() == 2
    assert apply(auth_client, nurse, types["cl"], date(2026, 3, 25), date(2026, 3, 26), half_day=True).status_code == 400
    assert apply(auth_client, nurse, types["el"], date(2026, 12, 30), date(2027, 1, 2)).status_code == 400  # across years


def test_warn_type_flags_short_requests_and_approval_rechecks_block_types(auth_client, nurse, types, hospital):
    r = apply(auth_client, nurse, types["sl"], date(2026, 5, 4), date(2026, 5, 13))  # 10 > 7
    assert r.status_code == 201 and r.data["short_balance"]
    assert auth_client.post(f"{HR}/leave-requests/{r.data['id']}/approve/").status_code == 200  # warn: approver decides
    assert auth_client.post(f"{HR}/leave-requests/{r.data['id']}/approve/").status_code == 400  # already approved

    el = apply(auth_client, nurse, types["el"], date(2026, 6, 1), date(2026, 6, 10)).data
    LeaveBalance.objects.create(hospital=hospital, employee=nurse, leave_type=types["el"], year=2026, adjustment=-15)  # e.g. encashed
    assert auth_client.post(f"{HR}/leave-requests/{el['id']}/approve/").status_code == 400  # only 3 left now
    b = balance(nurse, types["sl"], 2026)
    assert b["used"] == 10 and b["available"] == -3


def test_balance_summary_and_year_end_carry_forward_with_cap(auth_client, hospital, nurse, types):
    LeaveRequest.objects.create(hospital=hospital, employee=nurse, leave_type_master=types["el"], start_date=date(2025, 6, 2), end_date=date(2025, 6, 4), status="approved")
    LeaveBalance.objects.create(hospital=hospital, employee=nurse, leave_type=types["el"], year=2025, opening=25)
    summary = auth_client.get(f"{HR}/leave-balances/summary/", {"employee": nurse.pk, "year": 2025}).data[0]
    el = next(b for b in summary["balances"] if b["code"] == "EL")
    assert (el["opening"], el["accrued"], el["used"], el["available"]) == (25, 18, 3, 40)

    assert auth_client.post(f"{HR}/leave-balances/carry-forward/", {"year": timezone.localdate().year}, format="json").status_code == 400
    assert auth_client.post(f"{HR}/leave-balances/carry-forward/", {"year": 2025}, format="json").status_code == 200
    assert LeaveBalance.objects.get(employee=nurse, leave_type=types["el"], year=2026).opening == 30  # 40 capped at 30
    assert not LeaveBalance.objects.filter(employee=nurse, leave_type=types["cl"], year=2026).exists()  # CL lapses
    carry_forward(hospital.pk, 2025)  # re-run: same result, no duplicates
    assert LeaveBalance.objects.filter(employee=nurse, leave_type=types["el"], year=2026).count() == 1


def test_unpaid_leave_type_cuts_payroll_paid_days(auth_client, hospital, nurse, types):
    SalaryStructure.objects.create(hospital=hospital, employee=nurse, basic=20000, hra=5000)
    LeaveRequest.objects.create(hospital=hospital, employee=nurse, leave_type_master=types["lwp"], start_date=date(2026, 8, 10), end_date=date(2026, 8, 12), status="approved")
    LeaveRequest.objects.create(hospital=hospital, employee=nurse, leave_type_master=types["lwp"], start_date=date(2026, 8, 20), end_date=date(2026, 8, 20), half_day=True, status="approved")
    LeaveRequest.objects.create(hospital=hospital, employee=nurse, leave_type_master=types["el"], start_date=date(2026, 8, 25), end_date=date(2026, 8, 26), status="approved")
    run = auth_client.post(f"{HR}/payroll-runs/", {"month": "2026-08-01"}, format="json").data
    assert Payslip.objects.get(run_id=run["id"]).paid_days == Decimal("27.5")


# --- HR-2 dependents & staff rate ----------------------------------------------------

@pytest.fixture
def family(hospital, nurse):
    self_p = Patient.objects.create(hospital=hospital, first_name="Neha", last_name="Joshi", mobile="9811100401")
    child = Patient.objects.create(hospital=hospital, first_name="Aarav", last_name="Joshi", mobile="9811100402")
    nurse.patient = self_p
    nurse.save()
    return {"self": self_p, "child": child}


def test_dependents_link_patients_and_bills_default_to_staff_rate(auth_client, hospital, nurse, family):
    r = auth_client.post(f"{HR}/dependents/", {"employee": nurse.pk, "patient": family["child"].pk, "relationship": "child"}, format="json")
    assert r.status_code == 201 and r.data["patient_name"] == "Aarav Joshi"
    assert auth_client.post(f"{HR}/dependents/", {"employee": nurse.pk, "patient": family["self"].pk, "relationship": "spouse"}, format="json").status_code == 400
    link = auth_client.get(f"{HR}/dependents/staff-link/", {"patient": family["child"].pk}).data["link"]
    assert link["kind"] == "dependent" and link["employee_code"] == "N100" and link["relationship"] == "Child"
    assert staff_link(family["self"])["kind"] == "staff"

    t = ServiceTariff.objects.create(hospital=hospital, code="XR", name="X-ray", rate=800, category_rates={"staff": 400})
    bill = Bill.objects.create(hospital=hospital, patient=family["child"])
    assert bill.patient_category == "staff"
    added = auth_client.post(f"/api/v1/billing/bills/{bill.pk}/add-item/", {"tariff": t.pk}, format="json")
    assert added.status_code == 201 and BillItem.objects.get(bill=bill).unit_price == 400
    assert Bill.objects.create(hospital=hospital, patient=family["child"], patient_category="insurance").patient_category == "insurance"
    outsider = Patient.objects.create(hospital=hospital, first_name="Ravi", mobile="9811100403")
    assert Bill.objects.create(hospital=hospital, patient=outsider).patient_category == "general"


def test_staff_rate_stops_after_exit_or_dependent_end(hospital, nurse, family):
    dep = EmployeeDependent.objects.create(hospital=hospital, employee=nurse, patient=family["child"], relationship="child", valid_until=timezone.localdate() - timedelta(days=1))
    assert staff_link(family["child"]) is None
    dep.valid_until = None
    dep.save()
    assert staff_link(family["child"])["kind"] == "dependent"
    ExitRequest.objects.create(hospital=hospital, employee=nurse, reason="Moved", last_working_day=timezone.localdate() - timedelta(days=2), status="completed")
    assert staff_link(family["child"]) is None and staff_link(family["self"]) is None
    assert Bill.objects.create(hospital=hospital, patient=family["self"]).patient_category == "general"


def test_staff_category_is_offered_for_repricing(auth_client, hospital, family):
    bill = Bill.objects.create(hospital=hospital, patient=Patient.objects.create(hospital=hospital, first_name="Om", mobile="9811100404"), status="unpaid")
    assert auth_client.post(f"/api/v1/billing/bills/{bill.pk}/change-category/", {"category": "staff"}, format="json").status_code == 200


# --- HR-3 ESI including overtime --------------------------------------------------------

def test_overtime_is_paid_and_in_the_esi_wage_but_not_pf_or_eligibility(auth_client, hospital, nurse, user):
    st = SalaryStructure.objects.create(hospital=hospital, employee=nurse, basic=12000, hra=4000, special_allowance=4800)  # gross 20,800
    assert st.overtime_rate == Decimal("200.00")  # 2 x 20,800 / 208
    entry = auth_client.post(f"{HR}/overtime/", {"employee": nurse.pk, "date": "2026-08-05", "hours": "6", "reason": "Covering ICU"}, format="json").data
    OvertimeEntry.objects.create(hospital=hospital, employee=nurse, date=date(2026, 8, 6), hours=4)  # never approved
    approved = auth_client.post(f"{HR}/overtime/{entry['id']}/approve/").data
    assert approved["status"] == "approved"
    assert auth_client.patch(f"{HR}/overtime/{entry['id']}/", {"hours": "8"}, format="json").status_code == 400

    own = APIClient()
    own.force_authenticate(nurse.user)
    assign_role(nurse.user, user.role)
    mine = OvertimeEntry.objects.create(hospital=hospital, employee=nurse, date=date(2026, 8, 7), hours=2)
    assert own.post(f"{HR}/overtime/{mine.pk}/approve/").status_code == 403

    run = auth_client.post(f"{HR}/payroll-runs/", {"month": "2026-08-01"}, format="json").data
    slip = Payslip.objects.get(run_id=run["id"])
    assert slip.overtime_hours == 6 and Decimal(slip.earnings["overtime"]) == 1200
    assert slip.gross == Decimal("22000")  # over 21,000 with overtime, yet ESI still applies
    assert Decimal(slip.deductions["esi"]) == Decimal("165")  # 0.75% of 22,000
    assert Decimal(slip.employer_contributions["esi"]) == Decimal("715")  # 3.25% of 22,000
    assert Decimal(slip.deductions["pf"]) == Decimal("1440")  # 12% of basic only


def test_explicit_overtime_rate_and_no_esi_above_threshold():
    st = SalaryStructure(basic=20000, hra=5000, overtime_hourly_rate=Decimal("150"))
    calc = Payslip.compute(st, 30, 30, date(2026, 9, 1), overtime_hours=Decimal("2.5"))
    assert calc["earnings"]["overtime"] == "375"  # 2.5 x 150, rounded
    assert calc["deductions"]["esi"] == "0"  # regular gross 25,000 > 21,000


def test_front_desk_without_hr_role_can_see_the_staff_link(hospital, nurse, family, restricted_client):
    r = restricted_client.get(f"{HR}/dependents/staff-link/", {"patient": family["self"].pk})
    assert r.status_code == 200 and r.data["link"]["kind"] == "staff"
    assert restricted_client.get(f"{HR}/dependents/").status_code == 403


def test_esi_cover_continues_to_the_end_of_the_contribution_period(auth_client, hospital, nurse):
    from apps.hr.models import PayrollRun
    from apps.hr.models_hrm import esi_contribution_period

    assert esi_contribution_period(date(2026, 9, 1)) == date(2026, 4, 1)
    assert esi_contribution_period(date(2027, 2, 1)) == date(2026, 10, 1)
    st = SalaryStructure.objects.create(hospital=hospital, employee=nurse, basic=12000, hra=4000, special_allowance=4000)  # 20,000
    may = auth_client.post(f"{HR}/payroll-runs/", {"month": "2026-05-01"}, format="json").data
    assert Payslip.objects.get(run_id=may["id"]).esi_covered
    SalaryStructure.objects.filter(pk=st.pk).update(special_allowance=8000)  # raise: 24,000 from June
    june = Payslip.objects.get(run_id=auth_client.post(f"{HR}/payroll-runs/", {"month": "2026-06-01"}, format="json").data["id"])
    assert june.esi_covered and Decimal(june.deductions["esi"]) == Decimal("180")  # still covered: 0.75% of 24,000
    october = Payslip.objects.get(run_id=auth_client.post(f"{HR}/payroll-runs/", {"month": "2026-10-01"}, format="json").data["id"])
    assert not october.esi_covered and Decimal(october.deductions["esi"]) == 0  # new period, above the ceiling
    assert PayrollRun.objects.count() == 3
