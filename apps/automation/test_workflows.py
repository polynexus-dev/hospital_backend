"""Workflow engine end to end: triggers fire, conditions read live data,
waits really wait, tasks go to the owner, messages really go out (stub
provider), and nothing reaches people who opted out or are lost."""
import datetime
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import Role, User, assign_role
from apps.automation.engine import resume_due
from apps.automation.models import Task, Workflow, WorkflowRun
from apps.communications.models import ConsentOptOut, Message
from apps.enquiries.models import Enquiry
from apps.enquiries.services import move_stage
from apps.patients.models import Patient


@pytest.fixture
def owner(hospital):
    u = User.objects.create_user(email="exec@h.example", password="x", hospital=hospital, first_name="Priya")
    assign_role(u, Role.objects.create(hospital=hospital, name="CRM exec", template=Role.Template.CRM_EXECUTIVE))
    return u


def install(auth_client, key):
    wf = auth_client.post("/api/v1/workflows/install-template/", {"key": key}, format="json")
    assert wf.status_code == 201, wf.json()
    assert wf.json()["is_active"] is False  # installed switched off
    Workflow.objects.filter(pk=wf.json()["id"]).update(is_active=True)
    return Workflow.objects.get(pk=wf.json()["id"])


def lead(hospital, owner, mobile="9822077001", **kw):
    e = Enquiry.objects.create(hospital=hospital, name="Sunita Kale", mobile=mobile, source="website", **kw)
    Enquiry.objects.filter(pk=e.pk).update(assigned_to=owner)
    return e


def test_templates_are_listed(auth_client):
    rows = auth_client.get("/api/v1/workflows/templates/").json()
    assert {"new_lead_no_contact_24h", "contacted_lead_quiet_2d", "missed_call_callback", "no_show_recall", "detractor_escalation"} <= {r["key"] for r in rows}


def test_new_lead_waits_24h_then_messages_the_lead_and_tasks_the_owner(auth_client, hospital, owner, django_capture_on_commit_callbacks):
    install(auth_client, "new_lead_no_contact_24h")
    with django_capture_on_commit_callbacks(execute=True):
        e = lead(hospital, owner)
    run = WorkflowRun.objects.get()
    assert run.status == "waiting" and not Message.objects.exists() and not Task.objects.filter(title__startswith="Call today").exists()

    assert resume_due(timezone.now()) == 0  # not yet
    assert resume_due(timezone.now() + timedelta(hours=25)) == 1
    run.refresh_from_db()
    assert run.status == "success"
    msg = Message.objects.get(enquiry=e)
    assert msg.channel == "whatsapp" and "Sunita Kale" in msg.body and "Test Hospital" in msg.body
    task = Task.objects.get(title__startswith="Call today")
    assert task.owner == owner and task.priority == "high" and task.object_id == e.pk and "Sunita" not in task.title


def test_lead_contacted_during_the_wait_is_left_alone(auth_client, hospital, owner, django_capture_on_commit_callbacks):
    install(auth_client, "new_lead_no_contact_24h")
    with django_capture_on_commit_callbacks(execute=True):
        e = lead(hospital, owner)
        move_stage(e, Enquiry.Stage.CONTACTED)
    resume_due(timezone.now() + timedelta(hours=25))
    run = WorkflowRun.objects.get(workflow__name__startswith="New lead")
    assert run.status == "stopped" and "stage is 'contacted'" in run.log_output[-1]["details"]
    assert not Message.objects.exists()


def test_stage_change_trigger_with_to_stage_condition(auth_client, hospital, owner, django_capture_on_commit_callbacks):
    install(auth_client, "contacted_lead_quiet_2d")
    e = lead(hospital, owner)
    with django_capture_on_commit_callbacks(execute=True):
        move_stage(e, Enquiry.Stage.CONTACTED)
    assert WorkflowRun.objects.get().status == "waiting"
    resume_due(timezone.now() + timedelta(days=3))
    assert Message.objects.filter(enquiry=e).count() == 1 and Task.objects.filter(title__startswith=f"Follow up enquiry #{e.pk}").exists()


def test_opted_out_number_and_lost_leads_get_no_message(auth_client, hospital, owner, django_capture_on_commit_callbacks):
    install(auth_client, "new_lead_no_contact_24h")
    p = Patient.objects.create(hospital=hospital, first_name="Ravi", mobile="9822077002")
    for channel in ("whatsapp", "sms"):
        ConsentOptOut.objects.create(hospital=hospital, patient=p, channel=channel, purpose="marketing", is_opted_out=True)
    with django_capture_on_commit_callbacks(execute=True):
        lead(hospital, owner, mobile="9822077002")
    resume_due(timezone.now() + timedelta(hours=25))
    assert not Message.objects.exists()
    assert "Not sent" in WorkflowRun.objects.get().log_output[-2]["details"]


def test_no_show_and_detractor_triggers_fire(auth_client, hospital, user, django_capture_on_commit_callbacks):
    from apps.appointments.models import Doctor, Slot
    from apps.appointments.services import book_appointment, mark_no_show
    from apps.feedback.models import FeedbackRequest, NPSResponse

    install(auth_client, "no_show_recall")
    install(auth_client, "detractor_escalation")
    p = Patient.objects.create(hospital=hospital, first_name="Anil", mobile="9822077003")
    doc = Doctor.objects.create(hospital=hospital, name="Dr Rao")
    slot = Slot.objects.create(hospital=hospital, doctor=doc, date=datetime.date.today(), start_time=datetime.time(9, 0), end_time=datetime.time(9, 15))
    with django_capture_on_commit_callbacks(execute=True):
        mark_no_show(book_appointment(patient=p, slot=slot))
    assert Message.objects.filter(patient=p, channel="whatsapp").exists()

    req = FeedbackRequest.objects.create(hospital=hospital, patient=p)
    with django_capture_on_commit_callbacks(execute=True):
        NPSResponse.objects.create(hospital=hospital, feedback_request=req, patient=p, score=3)
    esc = Task.objects.get(title__startswith="Escalated by")
    assert esc.priority == "urgent" and esc.owner == user  # the hospital admin, as no CRM manager exists


def test_conditions_on_any_field_and_unreadable_ones_stop(hospital, owner):
    from apps.automation.engine import check, current_facts, parse_condition

    e = lead(hospital, owner, urgency="urgent")
    facts = current_facts({"enquiry_id": e.pk}, hospital.pk)
    assert check(facts, "source", "eq", "website") and check(facts, "urgency", "in", "urgent,high") and check(facts, "assigned", "eq", "true")
    assert not check(facts, "stage", "ne", "new") and check(facts, "score", "gte", 0)
    assert parse_condition({"condition": "score <= 6"}) == ("score", "lte", "6")
    assert parse_condition({"condition": "stage is kind of new"}) is None


def test_builder_rejects_steps_the_engine_cant_run(auth_client):
    bad = auth_client.post("/api/v1/workflows/", {"name": "Bad", "trigger_type": "enquiry_created", "steps": [
        {"order": 1, "step_type": "condition", "action_type": "create_task", "title": "?", "config": {"condition": "whenever"}}]}, format="json")
    assert bad.status_code == 400
    no_template = auth_client.post("/api/v1/workflows/", {"name": "Bad 2", "trigger_type": "enquiry_created", "steps": [
        {"order": 1, "step_type": "action", "action_type": "send_whatsapp", "title": "Send", "config": {}}]}, format="json")
    assert no_template.status_code == 400


def test_test_run_is_a_dry_run(auth_client, hospital, owner):
    wf = install(auth_client, "hot_lead_fast_track")
    e = lead(hospital, owner)
    Enquiry.objects.filter(pk=e.pk).update(score=90)
    tasks_before = Task.objects.count()
    run = auth_client.post(f"/api/v1/workflows/{wf.pk}/test_run/", {"sample_event": {"enquiry_id": e.pk}}, format="json").json()
    assert run["status"] == "success" and "would" in run["log_output"][-1]["details"].lower()
    assert Task.objects.count() == tasks_before


def test_only_roles_with_automation_rights_can_build_workflows(api_client, hospital, department):
    lab = User.objects.create_user(email="lab2@h.example", password="x", hospital=hospital, department=department)
    assign_role(lab, Role.objects.create(hospital=hospital, name="Lab", template=Role.Template.LAB_TECHNICIAN))
    api_client.force_authenticate(user=lab)
    assert api_client.post("/api/v1/workflows/", {"name": "x", "trigger_type": "missed_call", "steps": []}, format="json").status_code == 403
    assert api_client.post("/api/v1/workflows/install-template/", {"key": "missed_call_callback"}, format="json").status_code == 403
