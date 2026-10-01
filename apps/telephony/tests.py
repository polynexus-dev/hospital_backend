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


# --- HoduPBX Provider Adapter & Integration Tests ----------------------------

from unittest.mock import patch, MagicMock
from apps.telephony.adapters import HoduPBXProvider


def test_hodupbx_adapter_normalize_webhook_payload():
    provider = HoduPBXProvider()
    hodu_payload = {
        "tenant_id": "1000",
        "caller": "9820000020",
        "callee": "101",
        "start_date": "2026-09-26 10:00:00",
        "answer_date": "2026-09-26 10:00:05",
        "end_date": "2026-09-26 10:02:15",
        "duration": "130",
        "status": "answered",
        "callid": "hodu-call-999",
    }
    normalized = provider.normalize_webhook_payload(hodu_payload)

    assert normalized["direction"] == "inbound"
    assert normalized["status"] == "answered"
    assert normalized["from_number"] == "9820000020"
    assert normalized["to_number"] == "101"
    assert normalized["duration_seconds"] == 130
    assert normalized["provider_call_id"] == "hodu-call-999"
    assert normalized["started_at"] is not None
    assert normalized["answered_at"] is not None
    assert normalized["ended_at"] is not None


def test_hodupbx_adapter_normalize_missed_status():
    provider = HoduPBXProvider()
    hodu_payload = {
        "caller": "9820000021",
        "callee": "102",
        "start_date": "2026-09-26 11:00:00",
        "duration": "0",
        "status": "no_answer",
        "callid": "hodu-missed-101",
    }
    normalized = provider.normalize_webhook_payload(hodu_payload)

    assert normalized["status"] == "missed"
    assert normalized["from_number"] == "9820000021"
    assert normalized["duration_seconds"] == 0
    assert normalized["provider_call_id"] == "hodu-missed-101"


@patch("requests.post")
def test_hodupbx_adapter_initiate_call_success(mock_post, settings):
    settings.HODUPBX_BASE_URL = "https://pbx.hospital.example/hodupbx_api/v1.4"
    settings.HODUPBX_TOKEN_ID = "mock-token-123"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "status": "SUCCESS",
        "message": "Extension Speeddial Updated Successfully.",
        "data": {"ext_id": "722"},
    }
    mock_post.return_value = mock_resp

    provider = HoduPBXProvider()
    call_id = provider.initiate_call(from_number="101", to_number="9820000022")

    assert call_id == "722"
    mock_post.assert_called_once()
    assert "/api/info/speeddial" in mock_post.call_args[0][0]


@patch("requests.post")
def test_hodupbx_adapter_get_recording_url(mock_post, settings):
    settings.HODUPBX_BASE_URL = "https://pbx.hospital.example/hodupbx_api/v1.4"
    settings.HODUPBX_TOKEN_ID = "mock-token-123"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "status": "SUCCESS",
        "data": {
            "recordingPath": "https://pbx.hospital.example/recordings/call-999.wav"
        },
    }
    mock_post.return_value = mock_resp

    provider = HoduPBXProvider()
    rec_url = provider.get_recording_url("call-999")

    assert rec_url == "https://pbx.hospital.example/recordings/call-999.wav"
    mock_post.assert_called_once()
    assert "/api/info/TENANT/recordingPath" in mock_post.call_args[0][0]


@patch("requests.post")
def test_hodupbx_adapter_get_active_calls(mock_post, settings):
    settings.HODUPBX_BASE_URL = "https://pbx.hospital.example/hodupbx_api/v1.4"
    settings.HODUPBX_TOKEN_ID = "mock-token-123"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "status": "SUCCESS",
        "data": [
            {"caller_number": "9820000023", "callee_number": "101", "start_time": "2026-09-26 10:03:59"}
        ],
    }
    mock_post.return_value = mock_resp

    provider = HoduPBXProvider()
    active = provider.get_active_calls()

    assert len(active) == 1
    assert active[0]["caller_number"] == "9820000023"


@patch("requests.post")
def test_hodupbx_adapter_subscribe_extension(mock_post, settings):
    settings.HODUPBX_BASE_URL = "https://pbx.hospital.example/hodupbx_api/v1.4"
    settings.HODUPBX_TOKEN_ID = "mock-token-123"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"status": "SUCCESS", "message": "Extension Subscribed Successfully."}
    mock_post.return_value = mock_resp

    provider = HoduPBXProvider()
    success = provider.subscribe_extension("101", "pass123")

    assert success is True
    mock_post.assert_called_once()
    assert "/api/info/crmSubscribe" in mock_post.call_args[0][0]


@pytest.mark.django_db
def test_telephony_webhook_with_hodupbx_provider(api_client, hospital, settings):
    settings.TELEPHONY_PROVIDER = "hodupbx"

    hodu_webhook_body = {
        "caller": "9820000099",
        "callee": "105",
        "start_date": "2026-09-26 12:00:00",
        "answer_date": "2026-09-26 12:00:05",
        "end_date": "2026-09-26 12:03:00",
        "duration": "175",
        "status": "answered",
        "callid": "hodu-cdr-555",
    }

    response = api_client.post(
        f"/api/v1/webhooks/telephony/{hospital.id}/",
        hodu_webhook_body,
        format="json",
    )

    assert response.status_code == 201
    call_record = Call.objects.get(hospital=hospital, provider_call_id="hodu-cdr-555")
    assert call_record.from_number == "9820000099"
    assert call_record.to_number == "105"
    assert call_record.duration_seconds == 175
    assert call_record.status == Call.Status.ANSWERED


@pytest.mark.django_db
@patch("apps.telephony.adapters.HoduPBXProvider.get_active_calls")
def test_call_viewset_active_calls_endpoint(mock_active, auth_client, settings):
    settings.TELEPHONY_PROVIDER = "hodupbx"
    mock_active.return_value = [
        {"caller_number": "9820000088", "callee_number": "101", "start_time": "2026-09-26 12:30:00"}
    ]

    response = auth_client.get("/api/v1/calls/active-calls/")
    assert response.status_code == 200
    assert len(response.data) == 1
    assert response.data[0]["caller_number"] == "9820000088"

