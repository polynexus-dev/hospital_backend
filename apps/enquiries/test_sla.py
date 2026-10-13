"""Enquiry first-contact deadlines, escalation, assignment and the ageing report."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import Role, User, assign_role
from apps.automation.models import EscalationRule, Task
from apps.enquiries.models import Enquiry
from apps.enquiries.services import assign_enquiry, move_stage
from apps.enquiries.sla import escalate_all


def make_user(hospital, email, template):
    u = User.objects.create_user(email=email, password="x", hospital=hospital, first_name=email.split("@")[0].title())
    assign_role(u, Role.objects.create(hospital=hospital, name=f"{template} {email}", template=template))
    return u


@pytest.fixture
def team(hospital):
    return {
        "exec": make_user(hospital, "priya@h.example", Role.Template.CRM_EXECUTIVE),
        "manager": make_user(hospital, "meena@h.example", Role.Template.CRM_MANAGER),
        "lab": make_user(hospital, "lab@h.example", Role.Template.LAB_TECHNICIAN),
    }


def new_enquiry(hospital, **kw):
    return Enquiry.objects.create(**{"hospital": hospital, "name": "Asha", "mobile": "9822011100", "source": "website", **kw})


def test_new_enquiry_gets_a_first_contact_deadline_that_stops_on_contact(hospital, team):
    e = new_enquiry(hospital)
    assert e.sla_due_at is not None and abs((e.sla_due_at - e.created_at) - timedelta(minutes=240)) < timedelta(seconds=5)
    move_stage(e, Enquiry.Stage.CONTACTED, changed_by=team["exec"])
    e.refresh_from_db()
    assert e.sla_due_at is None and e.first_contacted_at is not None and e.escalation_level == 0


def test_department_rule_sets_the_deadline(hospital, team, department):
    EscalationRule.objects.create(hospital=hospital, name="Cardiology 30 min", applies_to="enquiry", department=department, escalate_after_minutes=30)
    e = new_enquiry(hospital, department=department)
    assert abs((e.sla_due_at - e.created_at) - timedelta(minutes=30)) < timedelta(seconds=5)


def test_leads_go_only_to_people_who_work_enquiries(hospital, team):
    e = new_enquiry(hospital)
    assert e.assigned_to in (team["exec"], team["manager"])
    for i in range(5):
        assert assign_enquiry(new_enquiry(hospital, mobile=f"98220111{i:02d}")) != team["lab"]


# NABH AAC.2.b (follow-up), and the CRM's response-time promise
def test_breach_tasks_the_owner_then_escalates_to_the_rule_and_reassigns(hospital, team):
    EscalationRule.objects.create(hospital=hospital, name="All enquiries", applies_to="enquiry", escalate_after_minutes=60, escalate_to=team["manager"])
    e = new_enquiry(hospital)
    Enquiry.objects.filter(pk=e.pk).update(assigned_to=team["exec"])
    e.refresh_from_db()
    now = e.sla_due_at + timedelta(minutes=1)

    assert escalate_all(now) == 1
    first = Task.objects.get()
    assert first.owner == team["exec"] and first.priority == "urgent" and first.title == f"First contact overdue: enquiry #{e.pk} (Website form)"
    assert "Asha" not in first.title  # task titles aren't encrypted
    assert escalate_all(now + timedelta(minutes=10)) == 0  # not again within the period

    assert escalate_all(now + timedelta(minutes=61)) == 1
    second = Task.objects.latest("id")
    assert second.owner == team["manager"] and second.title.startswith("Escalated")
    e.refresh_from_db()
    assert e.assigned_to == team["manager"] and e.escalation_level == 2
    assert e.assignment_changes.filter(reason__contains="escalation rule 'All enquiries'").exists()
    assert escalate_all(now + timedelta(days=2)) == 0  # stops after the manager


def test_without_a_rule_the_manager_is_told_but_nothing_is_reassigned(hospital, team):
    e = new_enquiry(hospital)
    Enquiry.objects.filter(pk=e.pk).update(assigned_to=team["exec"])
    e.refresh_from_db()
    t = e.sla_due_at + timedelta(minutes=1)
    escalate_all(t)
    escalate_all(t + timedelta(minutes=241))
    assert Task.objects.latest("id").owner == team["manager"]
    e.refresh_from_db()
    assert e.assigned_to == team["exec"]


def test_overdue_follow_up_raises_one_task_per_follow_up_date(hospital, team):
    e = new_enquiry(hospital)
    move_stage(e, Enquiry.Stage.CONTACTED)
    Enquiry.objects.filter(pk=e.pk).update(follow_up_date=timezone.localdate() - timedelta(days=1), assigned_to=team["exec"])
    assert escalate_all() == 1 and escalate_all() == 0
    assert Task.objects.get().title == f"Follow-up overdue: enquiry #{e.pk}"


def test_ageing_report_and_export(auth_client, hospital, team):
    old = new_enquiry(hospital)
    Enquiry.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=5), sla_due_at=timezone.now() - timedelta(days=4), assigned_to=team["exec"])
    fresh = new_enquiry(hospital, name="Ravi", mobile="9822011101")
    contacted = new_enquiry(hospital, name="Kiran", mobile="9822011102")
    move_stage(contacted, Enquiry.Stage.CONTACTED)
    Enquiry.objects.filter(pk=contacted.pk).update(follow_up_date=timezone.localdate() - timedelta(days=2))

    data = auth_client.get("/api/v1/enquiries/ageing/").json()
    s = data["summary"]
    assert (s["open"], s["untouched"], s["sla_breached"], s["follow_ups_overdue"]) == (3, 2, 1, 1)
    assert s["median_first_response_minutes_30d"] == 0 and s["contacted_within_4h_pct_30d"] == 100
    assert data["by_age"]["3_7d"] == 1 and data["rows"][0]["id"] == old.pk and data["rows"][0]["sla_breached"]
    assert data["rows"][1]["id"] == contacted.pk and data["rows"][1]["follow_up_overdue"]
    owner = next(o for o in data["by_owner"] if o["owner_id"] == team["exec"].pk)
    assert owner["sla_breached"] == 1 and owner["oldest_hours"] >= 120
    assert fresh.pk in [r["id"] for r in data["rows"]]

    xlsx = auth_client.get("/api/v1/enquiries/ageing/?export=xlsx")
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    text = auth_client.get("/api/v1/enquiries/ageing/?export=csv").content.decode()
    assert text.splitlines()[0].startswith("Enquiry #,Name,Source") and "Asha" in text


def test_ageing_report_needs_enquiry_access(api_client, team):
    api_client.force_authenticate(user=team["lab"])
    assert api_client.get("/api/v1/enquiries/ageing/").status_code == 403


@pytest.mark.parametrize("template,can_work,can_delete", [
    ("crm_super_admin", True, True), ("crm_manager", True, False), ("crm_executive", True, False),
    ("call_centre_executive", True, False), ("corporate_rm", True, False), ("marketing_manager", False, False), ("crm_auditor", False, False),
])
def test_crm_roles_grant_what_the_job_needs(hospital, template, can_work, can_delete):
    u = make_user(hospital, f"{template}@h.example", template)
    assert u.has_perm("enquiries.view_enquiry")
    assert u.has_perm("enquiries.change_enquiry") is can_work
    assert u.has_perm("enquiries.delete_enquiry") is can_delete
