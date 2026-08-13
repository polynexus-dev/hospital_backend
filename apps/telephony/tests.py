import datetime
from contextlib import suppress

import pytest
from django.utils import timezone

from apps.telephony.models import Call, CallbackTask, IVRRoute


@pytest.fixture
def call(hospital):
    call = Call.objects.create(
        hospital=hospital, direction=Call.Direction.INBOUND, status=Call.Status.ANSWERED,
        from_number="9820000001", started_at=timezone.now(),
    )
    yield call
    with suppress(Exception):
        call.delete()


@pytest.fixture
def other_call(other_hospital):
    call = Call.objects.create(
        hospital=other_hospital, direction=Call.Direction.INBOUND, status=Call.Status.ANSWERED,
        from_number="9820000002", started_at=timezone.now(),
    )
    yield call
    with suppress(Exception):
        call.delete()


# --- Call: model CRUD -------------------------------------------------------

@pytest.mark.django_db
def test_call_model_create_with_required_fields_only(hospital):
    call = Call.objects.create(hospital=hospital, direction=Call.Direction.INBOUND, status=Call.Status.MISSED, from_number="9820000003", started_at=timezone.now())
    assert call.pk is not None
    assert call.duration_seconds == 0
    assert call.consent_recorded is False


@pytest.mark.django_db
def test_call_model_update(call):
    call.status = Call.Status.MISSED
    call.save(update_fields=["status"])
    call.refresh_from_db()
    assert call.status == Call.Status.MISSED


@pytest.mark.django_db
def test_call_model_delete(hospital):
    call = Call.objects.create(hospital=hospital, direction=Call.Direction.INBOUND, status=Call.Status.ANSWERED, from_number="9820000004", started_at=timezone.now())
    call_id = call.id
    call.delete()
    assert not Call.objects.filter(pk=call_id).exists()


# --- Call: API CRUD ----------------------------------------------------------

@pytest.mark.django_db
def test_call_api_create_requires_direction_status_from_number_started_at(auth_client, hospital):
    response = auth_client.post("/api/v1/calls/", {
        "direction": "inbound", "status": "answered", "from_number": "9820000005",
        "started_at": timezone.now().isoformat(),
    }, format="json")

    assert response.status_code == 201
    created = Call.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id


@pytest.mark.django_db
def test_call_api_create_without_started_at_returns_400(auth_client):
    response = auth_client.post("/api/v1/calls/", {"direction": "inbound", "status": "answered", "from_number": "9820000006"}, format="json")
    assert response.status_code == 400
    assert "started_at" in response.data


@pytest.mark.django_db
def test_call_api_retrieve_update_delete(auth_client, call):
    retrieve = auth_client.get(f"/api/v1/calls/{call.id}/")
    assert retrieve.status_code == 200

    update = auth_client.patch(f"/api/v1/calls/{call.id}/", {"call_reason": "opd"}, format="json")
    assert update.status_code == 200
    call.refresh_from_db()
    assert call.call_reason == "opd"

    delete = auth_client.delete(f"/api/v1/calls/{call.id}/")
    assert delete.status_code == 204
    assert not Call.objects.filter(pk=call.id).exists()


@pytest.mark.django_db
def test_call_isolation(auth_client, other_call):
    assert auth_client.get(f"/api/v1/calls/{other_call.id}/").status_code == 404
    assert auth_client.patch(f"/api/v1/calls/{other_call.id}/", {"call_reason": "opd"}, format="json").status_code == 404
    assert auth_client.delete(f"/api/v1/calls/{other_call.id}/").status_code == 404
    ids = {row["id"] for row in auth_client.get("/api/v1/calls/").data["results"]}
    assert other_call.id not in ids


@pytest.mark.django_db
def test_click_to_call_creates_an_outbound_call(auth_client, hospital):
    response = auth_client.post("/api/v1/calls/click-to-call/", {"to_number": "9820000007"}, format="json")

    assert response.status_code == 201
    created = Call.objects.get(pk=response.data["id"])
    assert created.direction == Call.Direction.OUTBOUND
    assert created.to_number == "9820000007"
    assert created.hospital_id == hospital.id
    assert created.provider_call_id  # stubbed provider still returns an id


@pytest.mark.django_db
def test_operator_productivity_aggregates_by_operator(auth_client, hospital, user):
    Call.objects.create(hospital=hospital, direction=Call.Direction.INBOUND, status=Call.Status.ANSWERED, from_number="1", started_at=timezone.now(), operator=user)
    Call.objects.create(hospital=hospital, direction=Call.Direction.INBOUND, status=Call.Status.MISSED, from_number="2", started_at=timezone.now(), operator=user)

    response = auth_client.get("/api/v1/calls/operator-productivity/")

    assert response.status_code == 200
    row = next(r for r in response.data if r["operator_id"] == user.id)
    assert row["calls_handled"] == 2
    assert row["answered"] == 1
    assert row["missed"] == 1


@pytest.mark.django_db
def test_operator_productivity_excludes_other_hospitals_calls(auth_client, hospital, other_hospital, user, other_user):
    Call.objects.create(hospital=hospital, direction=Call.Direction.INBOUND, status=Call.Status.ANSWERED, from_number="1", started_at=timezone.now(), operator=user)
    Call.objects.create(hospital=other_hospital, direction=Call.Direction.INBOUND, status=Call.Status.ANSWERED, from_number="2", started_at=timezone.now(), operator=other_user)

    response = auth_client.get("/api/v1/calls/operator-productivity/")

    operator_ids = {row["operator_id"] for row in response.data}
    assert other_user.id not in operator_ids


# --- CallbackTask: model + API CRUD, actions, gotcha ------------------------

@pytest.mark.django_db
def test_callback_task_model_create_requires_phone_number_and_sla_due_at(hospital):
    task = CallbackTask.objects.create(hospital=hospital, phone_number="9820000008", sla_due_at=timezone.now() + datetime.timedelta(minutes=15))
    assert task.status == CallbackTask.Status.PENDING
    assert task.attempt_count == 0


@pytest.mark.django_db
def test_callback_task_api_create_without_sla_due_at_returns_400(auth_client):
    """sla_due_at is a required DateTimeField with no default — the
    serializer correctly requires it (unlike the feedback app's
    ServiceRecoveryTask, where the equivalent field is marked read-only by
    mistake — see apps/feedback/tests.py for that contrast)."""
    response = auth_client.post("/api/v1/callback-tasks/", {"phone_number": "9820000009"}, format="json")
    assert response.status_code == 400
    assert "sla_due_at" in response.data


@pytest.mark.django_db
def test_callback_task_api_create_and_delete(auth_client, hospital):
    create = auth_client.post("/api/v1/callback-tasks/", {
        "phone_number": "9820000010", "sla_due_at": (timezone.now() + datetime.timedelta(minutes=15)).isoformat(),
    }, format="json")
    assert create.status_code == 201
    created = CallbackTask.objects.get(pk=create.data["id"])
    assert created.hospital_id == hospital.id

    delete = auth_client.delete(f"/api/v1/callback-tasks/{created.id}/")
    assert delete.status_code == 204
    assert not CallbackTask.objects.filter(pk=created.id).exists()


@pytest.mark.django_db
def test_callback_task_claim_complete_log_attempt_lifecycle(auth_client, hospital, user):
    task = CallbackTask.objects.create(hospital=hospital, phone_number="9820000011", sla_due_at=timezone.now() + datetime.timedelta(minutes=15))

    claimed = auth_client.post(f"/api/v1/callback-tasks/{task.id}/claim/")
    assert claimed.status_code == 200
    assert claimed.data["status"] == "in_progress"
    task.refresh_from_db()
    assert task.owner_id == user.id

    attempted = auth_client.post(f"/api/v1/callback-tasks/{task.id}/log_attempt/")
    assert attempted.data["attempt_count"] == 1

    completed = auth_client.post(f"/api/v1/callback-tasks/{task.id}/complete/", {"notes": "Reached the patient."}, format="json")
    assert completed.status_code == 200
    assert completed.data["status"] == "done"
    task.refresh_from_db()
    assert task.resolved_at is not None
    assert task.notes == "Reached the patient."


@pytest.mark.django_db
def test_callback_task_isolation(auth_client, other_hospital):
    theirs = CallbackTask.objects.create(hospital=other_hospital, phone_number="9820000012", sla_due_at=timezone.now() + datetime.timedelta(minutes=15))

    assert auth_client.get(f"/api/v1/callback-tasks/{theirs.id}/").status_code == 404
    assert auth_client.post(f"/api/v1/callback-tasks/{theirs.id}/claim/").status_code == 404
    assert auth_client.delete(f"/api/v1/callback-tasks/{theirs.id}/").status_code == 404
    assert CallbackTask.objects.filter(pk=theirs.id).exists()


# --- IVRRoute: model + API CRUD (department is required, unlike most FKs) ---

@pytest.mark.django_db
def test_ivr_route_model_create_requires_department(hospital, department):
    route = IVRRoute.objects.create(hospital=hospital, department=department)
    assert route.language == "mr"
    assert route.is_active is True


@pytest.mark.django_db
def test_ivr_route_api_crud(auth_client, hospital, department):
    create = auth_client.post("/api/v1/ivr-routes/", {"department": department.id}, format="json")
    assert create.status_code == 201
    created = IVRRoute.objects.get(pk=create.data["id"])
    assert created.hospital_id == hospital.id

    update = auth_client.patch(f"/api/v1/ivr-routes/{created.id}/", {"dial_in_number": "1800-000-000"}, format="json")
    assert update.status_code == 200
    created.refresh_from_db()
    assert created.dial_in_number == "1800-000-000"

    delete = auth_client.delete(f"/api/v1/ivr-routes/{created.id}/")
    assert delete.status_code == 204
    assert not IVRRoute.objects.filter(pk=created.id).exists()


@pytest.mark.django_db
def test_ivr_route_api_create_without_department_returns_400(auth_client):
    response = auth_client.post("/api/v1/ivr-routes/", {}, format="json")
    assert response.status_code == 400
    assert "department" in response.data


@pytest.mark.django_db
def test_ivr_route_isolation(auth_client, other_hospital, other_department):
    theirs = IVRRoute.objects.create(hospital=other_hospital, department=other_department)
    assert auth_client.get(f"/api/v1/ivr-routes/{theirs.id}/").status_code == 404
    assert auth_client.delete(f"/api/v1/ivr-routes/{theirs.id}/").status_code == 404


# --- Telephony webhook: public, AllowAny, update_or_create + automation ----

@pytest.mark.django_db
def test_telephony_webhook_creates_a_call_from_a_provider_payload(api_client, hospital):
    payload = {
        "direction": "inbound", "status": "answered", "from_number": "9820000013",
        "to_number": "1800", "started_at": timezone.now().isoformat(), "call_id": "prov-call-1",
    }

    response = api_client.post(f"/api/v1/webhooks/telephony/{hospital.id}/", payload, format="json")

    assert response.status_code == 201
    assert Call.objects.filter(hospital=hospital, provider_call_id="prov-call-1").exists()


@pytest.mark.django_db
def test_telephony_webhook_without_started_at_returns_400(api_client, hospital):
    response = api_client.post(f"/api/v1/webhooks/telephony/{hospital.id}/", {"status": "missed", "call_id": "prov-call-2"}, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_telephony_webhook_is_idempotent_per_provider_call_id(api_client, hospital):
    payload = {
        "direction": "inbound", "status": "answered", "from_number": "9820000014",
        "started_at": timezone.now().isoformat(), "call_id": "prov-call-3",
    }
    api_client.post(f"/api/v1/webhooks/telephony/{hospital.id}/", payload, format="json")

    payload["status"] = "voicemail"
    api_client.post(f"/api/v1/webhooks/telephony/{hospital.id}/", payload, format="json")

    matching = Call.objects.filter(hospital=hospital, provider_call_id="prov-call-3")
    assert matching.count() == 1
    assert matching.first().status == "voicemail"


@pytest.mark.django_db
def test_telephony_webhook_missed_call_triggers_a_missed_call_workflow(api_client, hospital):
    from apps.automation.models import Workflow, WorkflowRun, WorkflowStep

    workflow = Workflow.objects.create(hospital=hospital, name="Missed call recall", trigger_type=Workflow.TriggerType.MISSED_CALL)
    WorkflowStep.objects.create(workflow=workflow, order=1, step_type=WorkflowStep.StepType.ACTION, action_type=WorkflowStep.ActionType.CREATE_TASK, title="Call back")

    payload = {"direction": "inbound", "status": "missed", "from_number": "9820000015", "started_at": timezone.now().isoformat(), "call_id": "prov-call-4"}
    response = api_client.post(f"/api/v1/webhooks/telephony/{hospital.id}/", payload, format="json")

    assert response.status_code == 201
    assert WorkflowRun.objects.filter(hospital=hospital, workflow=workflow, trigger_event="missed_call").exists()


@pytest.mark.django_db
def test_telephony_webhook_answered_call_does_not_trigger_missed_call_workflow(api_client, hospital):
    from apps.automation.models import Workflow, WorkflowRun

    workflow = Workflow.objects.create(hospital=hospital, name="Missed call recall", trigger_type=Workflow.TriggerType.MISSED_CALL)

    payload = {"direction": "inbound", "status": "answered", "from_number": "9820000016", "started_at": timezone.now().isoformat(), "call_id": "prov-call-5"}
    api_client.post(f"/api/v1/webhooks/telephony/{hospital.id}/", payload, format="json")

    assert not WorkflowRun.objects.filter(hospital=hospital, workflow=workflow).exists()
