import pytest

from apps.automation.engine import execute_workflow
from apps.automation.models import EscalationRule, Task, Workflow, WorkflowRun, WorkflowStep


# --- Task: model + API CRUD, claim/complete actions -------------------------

@pytest.mark.django_db
def test_task_model_create_with_required_fields_only(hospital):
    task = Task.objects.create(hospital=hospital, title="Follow up")
    assert task.status == Task.Status.PENDING
    assert task.priority == Task.Priority.NORMAL


@pytest.mark.django_db
def test_task_api_create_requires_only_title(auth_client, hospital, user):
    response = auth_client.post("/api/v1/tasks/", {"title": "Call back patient"}, format="json")

    assert response.status_code == 201
    created = Task.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
    assert created.created_by_id == user.id


@pytest.mark.django_db
def test_task_claim_and_complete_lifecycle(auth_client, hospital, user):
    task = Task.objects.create(hospital=hospital, title="X")

    claimed = auth_client.post(f"/api/v1/tasks/{task.id}/claim/")
    assert claimed.status_code == 200
    assert claimed.data["status"] == "in_progress"
    task.refresh_from_db()
    assert task.owner_id == user.id

    completed = auth_client.post(f"/api/v1/tasks/{task.id}/complete/")
    assert completed.status_code == 200
    assert completed.data["status"] == "done"
    task.refresh_from_db()
    assert task.completed_at is not None


@pytest.mark.django_db
def test_task_api_update_and_delete(auth_client, hospital):
    task = Task.objects.create(hospital=hospital, title="X")

    update = auth_client.patch(f"/api/v1/tasks/{task.id}/", {"priority": "urgent"}, format="json")
    assert update.status_code == 200
    task.refresh_from_db()
    assert task.priority == "urgent"

    delete = auth_client.delete(f"/api/v1/tasks/{task.id}/")
    assert delete.status_code == 204
    assert not Task.objects.filter(pk=task.id).exists()


@pytest.mark.django_db
def test_task_isolation(auth_client, other_hospital):
    theirs = Task.objects.create(hospital=other_hospital, title="Theirs")
    assert auth_client.get(f"/api/v1/tasks/{theirs.id}/").status_code == 404
    assert auth_client.post(f"/api/v1/tasks/{theirs.id}/claim/").status_code == 404
    assert auth_client.delete(f"/api/v1/tasks/{theirs.id}/").status_code == 404


# --- EscalationRule: model + API CRUD ---------------------------------------

@pytest.mark.django_db
def test_escalation_rule_api_create_requires_name_applies_to_and_minutes(auth_client, hospital):
    response = auth_client.post("/api/v1/escalation-rules/", {
        "name": "Enquiry SLA breach", "applies_to": "enquiry", "escalate_after_minutes": 30,
    }, format="json")

    assert response.status_code == 201
    created = EscalationRule.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
    assert created.is_active is True


@pytest.mark.django_db
def test_escalation_rule_api_create_missing_required_fields_returns_400(auth_client):
    response = auth_client.post("/api/v1/escalation-rules/", {"name": "X"}, format="json")
    assert response.status_code == 400
    assert "applies_to" in response.data and "escalate_after_minutes" in response.data


@pytest.mark.django_db
def test_escalation_rule_api_update_and_delete(auth_client, hospital):
    rule = EscalationRule.objects.create(hospital=hospital, name="X", applies_to="enquiry", escalate_after_minutes=30)

    update = auth_client.patch(f"/api/v1/escalation-rules/{rule.id}/", {"is_active": False}, format="json")
    assert update.status_code == 200
    rule.refresh_from_db()
    assert rule.is_active is False

    delete = auth_client.delete(f"/api/v1/escalation-rules/{rule.id}/")
    assert delete.status_code == 204
    assert not EscalationRule.objects.filter(pk=rule.id).exists()


@pytest.mark.django_db
def test_escalation_rule_isolation(auth_client, other_hospital):
    theirs = EscalationRule.objects.create(hospital=other_hospital, name="Theirs", applies_to="enquiry", escalate_after_minutes=15)
    assert auth_client.get(f"/api/v1/escalation-rules/{theirs.id}/").status_code == 404
    assert auth_client.delete(f"/api/v1/escalation-rules/{theirs.id}/").status_code == 404


# --- Workflow: nested steps create/replace-on-update, test_run action ------

@pytest.mark.django_db
def test_workflow_api_create_with_nested_steps(auth_client, hospital, user):
    response = auth_client.post("/api/v1/workflows/", {
        "name": "Missed call recall",
        "trigger_type": "missed_call",
        "steps": [
            {"step_type": "action", "action_type": "create_task", "title": "Call back", "config": {"title": "Recall missed caller"}},
        ],
    }, format="json")

    assert response.status_code == 201
    workflow = Workflow.objects.get(pk=response.data["id"])
    assert workflow.hospital_id == hospital.id
    assert workflow.created_by_id == user.id
    assert workflow.steps.count() == 1
    assert workflow.steps.first().order == 1  # auto-numbered


@pytest.mark.django_db
def test_workflow_api_create_without_steps_is_valid(auth_client, hospital):
    response = auth_client.post("/api/v1/workflows/", {"name": "Empty workflow"}, format="json")
    assert response.status_code == 201
    assert Workflow.objects.get(pk=response.data["id"]).steps.count() == 0


@pytest.mark.django_db
def test_workflow_api_update_replaces_all_steps_not_merges(auth_client, hospital):
    workflow = Workflow.objects.create(hospital=hospital, name="X")
    WorkflowStep.objects.create(workflow=workflow, order=1, title="Old step")

    response = auth_client.patch(f"/api/v1/workflows/{workflow.id}/", {
        "steps": [{"step_type": "action", "action_type": "send_whatsapp", "title": "New step"}],
    }, format="json")

    assert response.status_code == 200
    remaining = list(workflow.steps.all())
    assert len(remaining) == 1
    assert remaining[0].title == "New step"


@pytest.mark.django_db
def test_workflow_isolation(auth_client, other_hospital):
    theirs = Workflow.objects.create(hospital=other_hospital, name="Theirs")
    assert auth_client.get(f"/api/v1/workflows/{theirs.id}/").status_code == 404
    assert auth_client.delete(f"/api/v1/workflows/{theirs.id}/").status_code == 404


@pytest.mark.django_db
def test_workflow_test_run_action_executes_and_returns_a_run(auth_client, hospital):
    workflow = Workflow.objects.create(hospital=hospital, name="Recall", trigger_type=Workflow.TriggerType.MISSED_CALL)
    WorkflowStep.objects.create(workflow=workflow, order=1, step_type=WorkflowStep.StepType.ACTION, action_type=WorkflowStep.ActionType.CREATE_TASK)

    response = auth_client.post(f"/api/v1/workflows/{workflow.id}/test_run/", {"sample_event": {"score": 4}}, format="json")

    assert response.status_code == 200
    assert response.data["status"] == "success"
    assert WorkflowRun.objects.filter(hospital=hospital, workflow=workflow).exists()


# --- WorkflowRun: read-only viewset ------------------------------------------

@pytest.mark.django_db
def test_workflow_run_viewset_has_no_create_endpoint(auth_client, hospital):
    workflow = Workflow.objects.create(hospital=hospital, name="X")
    response = auth_client.post("/api/v1/workflow-runs/", {"workflow": workflow.id, "trigger_event": "missed_call"}, format="json")
    assert response.status_code == 405


@pytest.mark.django_db
def test_workflow_run_isolation(auth_client, other_hospital):
    other_workflow = Workflow.objects.create(hospital=other_hospital, name="Theirs")
    theirs = WorkflowRun.objects.create(hospital=other_hospital, workflow=other_workflow, trigger_event="missed_call")
    assert auth_client.get(f"/api/v1/workflow-runs/{theirs.id}/").status_code == 404
    ids = {row["id"] for row in auth_client.get("/api/v1/workflow-runs/").data["results"]}
    assert theirs.id not in ids


# --- engine.execute_workflow: condition evaluation, action execution -------

@pytest.mark.django_db
def test_execute_workflow_only_matches_active_workflows_for_the_trigger_and_hospital(hospital, other_hospital):
    matching = Workflow.objects.create(hospital=hospital, name="Match", trigger_type=Workflow.TriggerType.MISSED_CALL, is_active=True)
    Workflow.objects.create(hospital=hospital, name="Inactive", trigger_type=Workflow.TriggerType.MISSED_CALL, is_active=False)
    Workflow.objects.create(hospital=hospital, name="Wrong trigger", trigger_type=Workflow.TriggerType.NPS_DETRACTOR, is_active=True)
    Workflow.objects.create(hospital=other_hospital, name="Other hospital", trigger_type=Workflow.TriggerType.MISSED_CALL, is_active=True)

    runs = execute_workflow("missed_call", {"from_number": "9800000000"}, hospital.id)

    assert len(runs) == 1
    assert runs[0].workflow_id == matching.id


@pytest.mark.django_db
def test_execute_workflow_condition_step_skips_when_score_exceeds_threshold(hospital):
    workflow = Workflow.objects.create(hospital=hospital, name="Detractor follow-up", trigger_type=Workflow.TriggerType.NPS_DETRACTOR)
    WorkflowStep.objects.create(workflow=workflow, order=1, step_type=WorkflowStep.StepType.CONDITION, config={"condition": "score <= 6"})
    WorkflowStep.objects.create(workflow=workflow, order=2, step_type=WorkflowStep.StepType.ACTION, action_type=WorkflowStep.ActionType.CREATE_TASK)

    runs = execute_workflow("nps_detractor", {"score": 9}, hospital.id)

    assert runs[0].log_output[0]["status"] == "skipped"
    assert not Task.objects.filter(hospital=hospital).exists()  # action step never ran


@pytest.mark.django_db
def test_execute_workflow_condition_step_passes_when_score_meets_threshold(hospital):
    workflow = Workflow.objects.create(hospital=hospital, name="Detractor follow-up", trigger_type=Workflow.TriggerType.NPS_DETRACTOR)
    WorkflowStep.objects.create(workflow=workflow, order=1, step_type=WorkflowStep.StepType.CONDITION, config={"condition": "score <= 6"})
    WorkflowStep.objects.create(workflow=workflow, order=2, step_type=WorkflowStep.StepType.ACTION, action_type=WorkflowStep.ActionType.CREATE_TASK, config={"title": "Recover detractor"})

    runs = execute_workflow("nps_detractor", {"score": 2}, hospital.id)

    assert runs[0].status == WorkflowRun.Status.SUCCESS
    assert Task.objects.filter(hospital=hospital, title="Recover detractor").exists()


@pytest.mark.django_db
def test_execute_workflow_create_task_action_sets_high_priority_when_urgent_in_event_data(hospital):
    workflow = Workflow.objects.create(hospital=hospital, name="Urgent handler", trigger_type=Workflow.TriggerType.MISSED_CALL)
    WorkflowStep.objects.create(workflow=workflow, order=1, step_type=WorkflowStep.StepType.ACTION, action_type=WorkflowStep.ActionType.CREATE_TASK)

    execute_workflow("missed_call", {"note": "urgent callback requested"}, hospital.id)

    task = Task.objects.get(hospital=hospital)
    assert task.priority == Task.Priority.HIGH


@pytest.mark.django_db
def test_execute_workflow_send_whatsapp_action_only_logs_no_real_send(hospital):
    """Documents real (limited) behavior: SEND_WHATSAPP/SEND_SMS/ESCALATE/
    WAIT_DELAY steps only append a log entry — they never call
    apps.communications.adapters to actually dispatch anything."""
    workflow = Workflow.objects.create(hospital=hospital, name="Notify", trigger_type=Workflow.TriggerType.MISSED_CALL)
    WorkflowStep.objects.create(workflow=workflow, order=1, step_type=WorkflowStep.StepType.ACTION, action_type=WorkflowStep.ActionType.SEND_WHATSAPP, config={"template": "missed_call_recall"})

    runs = execute_workflow("missed_call", {}, hospital.id)

    log = runs[0].log_output[0]
    assert "queued for dispatch" in log["details"]
    from apps.communications.models import Message
    assert not Message.objects.filter(hospital=hospital).exists()


@pytest.mark.django_db
def test_execute_workflow_always_creates_a_run_even_with_no_steps(hospital):
    Workflow.objects.create(hospital=hospital, name="Empty", trigger_type=Workflow.TriggerType.MISSED_CALL)
    runs = execute_workflow("missed_call", {}, hospital.id)
    assert len(runs) == 1
    assert runs[0].log_output == []
    assert runs[0].status == WorkflowRun.Status.SUCCESS
