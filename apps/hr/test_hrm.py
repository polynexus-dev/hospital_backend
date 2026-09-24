from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User, assign_role
from apps.hr.models import Employee, Shift


@pytest.fixture
def nurse(hospital, user):
    u = User.objects.create_user(email="nurse1@x.example", password="x", hospital=hospital, first_name="Neha")
    return Employee.objects.create(hospital=hospital, user=u, employee_code="N001", designation="Staff Nurse")


def test_payroll_statutory_deductions_and_maker_checker(auth_client, hospital, user, nurse):
    auth_client.post("/api/v1/hr/salary-structures/", {"employee": nurse.pk, "basic": "15000", "hra": "3000", "special_allowance": "2000"}, format="json")
    month = date(2026, 8, 1)
    from apps.hr.models import Attendance

    Attendance.objects.create(hospital=hospital, employee=nurse, date=date(2026, 8, 5), status="absent")
    run = auth_client.post("/api/v1/hr/payroll-runs/", {"month": "2026-08-15"}, format="json").json()
    slip = auth_client.get(f"/api/v1/hr/payroll-runs/{run['id']}/payslips/").json()[0]
    assert Decimal(slip["paid_days"]) == Decimal("30.0")
    f = Decimal(30) / Decimal(31)
    assert Decimal(slip["deductions"]["pf"]) == (Decimal(15000) * f * Decimal("0.12")).quantize(Decimal("1"))
    assert Decimal(slip["deductions"]["esi"]) > 0 and Decimal(slip["deductions"]["professional_tax"]) == 200
    assert auth_client.post(f"/api/v1/hr/payroll-runs/{run['id']}/approve/").status_code == 403
    approver = User.objects.create_user(email="hr2@x.example", password="x", hospital=hospital)
    assign_role(approver, user.role)
    c2 = APIClient()
    c2.force_authenticate(user=approver)
    assert c2.post(f"/api/v1/hr/payroll-runs/{run['id']}/approve/").json()["status"] == "approved"
    assert auth_client.post(f"/api/v1/hr/payroll-runs/{run['id']}/share_payslips/").json()["shared"] == 1


def test_roster_publish_checks_duty_rules_and_notifies(auth_client, hospital, nurse):
    from apps.clinical.models import ClinicalAlert

    auth_client.post("/api/v1/hr/duty-rules/", {"name": "Nursing", "applies_to_designation": "nurse", "max_consecutive_nights": 2}, format="json")
    start = timezone.localdate()
    for i in range(3):
        Shift.objects.create(hospital=hospital, employee=nurse, shift_date=start + timedelta(days=i), shift_type="night")
    body = {"period_start": str(start), "period_end": str(start + timedelta(days=6))}
    res = auth_client.post("/api/v1/hr/roster-publications/", body, format="json")
    assert res.status_code == 400 and "consecutive nights" in str(res.json())
    ok = auth_client.post("/api/v1/hr/roster-publications/", {**body, "override": True}, format="json").json()
    assert ok["notified_count"] == 1
    assert ClinicalAlert.objects.filter(target_user=nurse.user, title__startswith="Your roster").exists()


def test_recruitment_exit_training(auth_client, hospital, nurse):
    op = auth_client.post("/api/v1/hr/job-openings/", {"title": "ICU nurse", "positions": 2}, format="json").json()
    assert auth_client.post("/api/v1/hr/candidates/", {"opening": op["id"], "name": "Ria"}, format="json").status_code == 400
    auth_client.post(f"/api/v1/hr/job-openings/{op['id']}/approve/")
    cand = auth_client.post("/api/v1/hr/candidates/", {"opening": op["id"], "name": "Ria"}, format="json").json()
    assert auth_client.post(f"/api/v1/hr/candidates/{cand['id']}/move/", {"stage": "joined"}, format="json").status_code == 400

    ex = auth_client.post("/api/v1/hr/exits/", {"employee": nurse.pk, "reason": "Relocation", "last_working_day": str(timezone.localdate() + timedelta(days=30))}, format="json").json()
    assert auth_client.post(f"/api/v1/hr/exits/{ex['id']}/complete/").status_code == 400
    for d in ex["clearances"]:
        auth_client.post(f"/api/v1/hr/exits/{ex['id']}/clear/", {"department": d}, format="json")
    assert auth_client.post(f"/api/v1/hr/exits/{ex['id']}/complete/").json()["status"] == "completed"
    nurse.user.refresh_from_db()
    assert nurse.user.is_active is False

    assert any(r["employee_code"] == "N001" for r in auth_client.get("/api/v1/hr/trainings/pending_induction/").json())
    prog = auth_client.post("/api/v1/hr/trainings/", {"title": "Induction", "kind": "induction", "scheduled_at": timezone.now().isoformat(), "invitees": [nurse.pk]}, format="json").json()
    res = auth_client.post(f"/api/v1/hr/trainings/{prog['id']}/record/", {"attendees": [{"employee": nurse.pk, "attended": True, "feedback_rating": 4}]}, format="json").json()
    assert res["attendance_summary"] == {"invited": 1, "attended": 1, "avg_feedback": 4.0}
    assert not any(r["employee_code"] == "N001" for r in auth_client.get("/api/v1/hr/trainings/pending_induction/").json())


def test_appraisal_overall(auth_client, nurse):
    a = auth_client.post("/api/v1/hr/appraisals/", {"employee": nurse.pk, "period": "FY2025-26", "ratings": {"clinical": 4, "communication": 5, "teamwork": 3}}, format="json").json()
    assert Decimal(a["overall_rating"]) == Decimal("4.00")
