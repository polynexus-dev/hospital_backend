"""MedNet M2.22 HR, second batch: salary-structure Excel with gross amount and
payable days, employee prefix on the payslip print, and the dependent
linking policy (allowed count, relation age limits, unmarried only)."""
from datetime import date

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.core.xlsx import read_xlsx
from apps.hr.models import Attendance, DependentAllowance, DependentRelationRule, Employee, EmployeeDependent, PayrollRun, SalaryStructure
from apps.patients.models import Patient

HR = "/api/v1/hr"


@pytest.fixture
def nurse(hospital):
    u = User.objects.create_user(email="neha.b@x.example", password="x", hospital=hospital, first_name="Neha", last_name="Bhosale")
    emp = Employee.objects.create(hospital=hospital, user=u, employee_code="PNX1001", designation="Staff Nurse", name_prefix="Ms.",
                                  date_of_joining=date(2020, 4, 1), marital_status="married")
    SalaryStructure.objects.create(hospital=hospital, employee=emp, basic=20000, hra=8000, special_allowance=4000, other_allowances=1000, esi_applicable=False)
    return emp


def test_salary_structure_excel_has_gross_and_payable_days(auth_client, nurse):
    Attendance.objects.create(hospital=nurse.hospital, employee=nurse, date=date(2026, 9, 10), status="absent")
    r = auth_client.get(f"{HR}/salary-structures/export/?month=2026-09")
    assert r.status_code == 200 and r["Content-Type"].startswith("application/vnd.openxmlformats")
    head, row = read_xlsx(r.content)[:2]
    assert "Gross amount" in head and "Payable days" in head and "Prefix" in head
    by = dict(zip(head, row))
    assert by["Employee code"] == "PNX1001" and by["Prefix"] == "Ms." and by["Gross amount"] == "33000" and by["Payable days"] == "29" and by["Days in month"] == "30"
    assert auth_client.get(f"{HR}/salary-structures/export/?month=bad").status_code == 400


def test_payslip_pdf_and_email_carry_the_prefix(auth_client, nurse, user):
    run_id = auth_client.post(f"{HR}/payroll-runs/", {"month": "2026-09-01"}, format="json").data["id"]
    r = auth_client.get(f"{HR}/payroll-runs/{run_id}/payslips-pdf/")
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf" and r.content.startswith(b"%PDF")
    approver = User.objects.create_user(email="approver@x.example", password="x", hospital=nurse.hospital)
    approver.role = user.role
    approver.save()
    c = APIClient()
    c.force_authenticate(approver)
    assert c.post(f"{HR}/payroll-runs/{run_id}/approve/").status_code == 200
    mail.outbox.clear()
    assert auth_client.post(f"{HR}/payroll-runs/{run_id}/share_payslips/").data["shared"] == 1
    msg = mail.outbox[0]
    assert "Dear Ms. Neha Bhosale" in msg.body and msg.attachments[0][0] == "payslip-2026-09.pdf"
    assert PayrollRun.objects.get(pk=run_id).payslips.get().shared_at is not None


def _patient(hospital, first, dob):
    return Patient.objects.create(hospital=hospital, first_name=first, last_name="Bhosale", date_of_birth=dob, mobile=f"+91900000{abs(hash(first)) % 10000:04d}")


def add(client, emp, patient, relationship, **kw):
    return client.post(f"{HR}/dependents/", {"employee": emp.pk, "patient": patient.pk, "relationship": relationship, **kw}, format="json")


def test_relation_age_limit_sets_cover_end_and_refuses_over_age(auth_client, nurse):
    DependentRelationRule.objects.create(hospital=nurse.hospital, relationship="child", max_age=21, unmarried_only=True)
    today = timezone.localdate()
    son = _patient(nurse.hospital, "Aarav", date(today.year - 10, 6, 15))
    r = add(auth_client, nurse, son, "child")
    assert r.status_code == 201, r.data
    assert r.data["valid_until"] == str(date(today.year + 11, 6, 15))  # cover ends on the 21st birthday
    older = _patient(nurse.hospital, "Rohan", date(today.year - 22, 1, 1))
    assert add(auth_client, nurse, older, "child").status_code == 400
    married = _patient(nurse.hospital, "Riya", date(today.year - 19, 1, 1))
    assert "is_married" in add(auth_client, nurse, married, "child", is_married=True).data


def test_allowed_dependent_count_most_specific_rule_wins(auth_client, nurse):
    DependentAllowance.objects.create(hospital=nurse.hospital, max_dependents=1)
    DependentAllowance.objects.create(hospital=nurse.hospital, marital_status="married", max_dependents=2)
    a, b, c = (_patient(nurse.hospital, n, date(1960, 1, 1)) for n in ("Sunita", "Mohan", "Lata"))
    assert add(auth_client, nurse, a, "parent").status_code == 201
    assert add(auth_client, nurse, b, "parent").status_code == 201  # married staff: 2
    r = add(auth_client, nurse, c, "parent_in_law")
    assert r.status_code == 400 and "at most 2" in str(r.data["employee"])
    EmployeeDependent.objects.filter(employee=nurse, patient=a).update(is_active=False)  # ending one frees a place
    assert add(auth_client, nurse, c, "parent_in_law").status_code == 201


def test_relation_max_count(auth_client, nurse):
    DependentRelationRule.objects.create(hospital=nurse.hospital, relationship="spouse", max_count=1)
    assert add(auth_client, nurse, _patient(nurse.hospital, "Vikram", date(1985, 1, 1)), "spouse").status_code == 201
    assert add(auth_client, nurse, _patient(nurse.hospital, "Amit", date(1985, 1, 1)), "spouse").status_code == 400


def test_no_policy_means_no_limits(auth_client, nurse):
    for n in ("A1", "A2", "A3"):
        assert add(auth_client, nurse, _patient(nurse.hospital, n, None), "other").status_code == 201
