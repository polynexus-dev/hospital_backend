import datetime
from contextlib import suppress

import pytest
from django.utils import timezone

from apps.feedback.models import Complaint, FeedbackRequest, NPSResponse, ServiceRecoveryTask
from apps.patients.models import Patient


@pytest.fixture
def patient(hospital):
    p = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9833000001")
    yield p
    with suppress(Exception):
        p.delete()


@pytest.fixture
def other_patient(other_hospital):
    p = Patient.objects.create(hospital=other_hospital, first_name="Theirs", mobile="9833000002")
    yield p
    with suppress(Exception):
        p.delete()


@pytest.fixture
def feedback_request(hospital, patient):
    fr = FeedbackRequest.objects.create(hospital=hospital, patient=patient)
    yield fr
    with suppress(Exception):
        fr.delete()


# --- FeedbackRequest: model + API CRUD -------------------------------------

@pytest.mark.django_db
def test_feedback_request_model_create_auto_generates_a_token(hospital, patient):
    fr = FeedbackRequest.objects.create(hospital=hospital, patient=patient)
    assert fr.token
    assert fr.status == FeedbackRequest.Status.SENT


@pytest.mark.django_db
def test_feedback_request_api_create_requires_only_patient(auth_client, hospital, patient):
    response = auth_client.post("/api/v1/feedback-requests/", {"patient": patient.id}, format="json")
    assert response.status_code == 201
    created = FeedbackRequest.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
    assert created.token


@pytest.mark.django_db
def test_feedback_request_api_update_and_delete(auth_client, hospital, department, feedback_request):
    # `status` is read_only on FeedbackRequestSerializer (only the
    # SubmitFeedbackView / signals move it) — PATCH it and confirm it's
    # silently ignored, not erroring, then patch an actually-writable field.
    ignored = auth_client.patch(f"/api/v1/feedback-requests/{feedback_request.id}/", {"status": "expired"}, format="json")
    assert ignored.status_code == 200
    feedback_request.refresh_from_db()
    assert feedback_request.status == FeedbackRequest.Status.SENT

    update = auth_client.patch(f"/api/v1/feedback-requests/{feedback_request.id}/", {"department": department.id}, format="json")
    assert update.status_code == 200
    feedback_request.refresh_from_db()
    assert feedback_request.department_id == department.id

    delete = auth_client.delete(f"/api/v1/feedback-requests/{feedback_request.id}/")
    assert delete.status_code == 204
    assert not FeedbackRequest.objects.filter(pk=feedback_request.id).exists()


@pytest.mark.django_db
def test_feedback_request_isolation(auth_client, other_hospital, other_patient):
    theirs = FeedbackRequest.objects.create(hospital=other_hospital, patient=other_patient)
    assert auth_client.get(f"/api/v1/feedback-requests/{theirs.id}/").status_code == 404
    assert auth_client.delete(f"/api/v1/feedback-requests/{theirs.id}/").status_code == 404


# --- NPSResponse: category auto-computed, read-only viewset ----------------

@pytest.mark.django_db
@pytest.mark.parametrize("score,expected_category", [(10, NPSResponse.Category.PROMOTER), (9, NPSResponse.Category.PROMOTER), (8, NPSResponse.Category.PASSIVE), (7, NPSResponse.Category.PASSIVE), (6, NPSResponse.Category.DETRACTOR), (0, NPSResponse.Category.DETRACTOR)])
def test_nps_response_category_is_auto_computed_from_score(hospital, patient, feedback_request, score, expected_category):
    response = NPSResponse.objects.create(hospital=hospital, feedback_request=feedback_request, patient=patient, score=score, category="ignored-on-save")
    assert response.category == expected_category


@pytest.mark.django_db
def test_nps_response_viewset_has_no_create_endpoint(auth_client, hospital, patient, feedback_request):
    response = auth_client.post("/api/v1/nps-responses/", {"feedback_request": feedback_request.id, "patient": patient.id, "score": 9}, format="json")
    assert response.status_code == 405


@pytest.mark.django_db
def test_nps_response_by_department_rollup(auth_client, hospital, patient, feedback_request, department):
    NPSResponse.objects.create(hospital=hospital, feedback_request=feedback_request, patient=patient, department=department, score=9)

    response = auth_client.get("/api/v1/nps-responses/by-department/")

    row = next(r for r in response.data if r["department__name"] == department.name)
    assert row["total"] == 1
    assert row["promoters"] == 1


@pytest.mark.django_db
def test_nps_response_isolation(auth_client, other_hospital, other_patient):
    their_request = FeedbackRequest.objects.create(hospital=other_hospital, patient=other_patient)
    theirs = NPSResponse.objects.create(hospital=other_hospital, feedback_request=their_request, patient=other_patient, score=9)
    assert auth_client.get(f"/api/v1/nps-responses/{theirs.id}/").status_code == 404


# --- Complaint: model + API CRUD + close action -----------------------------

@pytest.mark.django_db
def test_complaint_api_create_requires_patient_and_description(auth_client, hospital, patient):
    response = auth_client.post("/api/v1/complaints/", {"patient": patient.id, "description": "Long wait time at OPD."}, format="json")
    assert response.status_code == 201
    created = Complaint.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
    assert created.status == Complaint.Status.OPEN


@pytest.mark.django_db
def test_complaint_close_action_sets_status_and_root_cause(auth_client, hospital, patient):
    complaint = Complaint.objects.create(hospital=hospital, patient=patient, description="Billing error")

    response = auth_client.post(f"/api/v1/complaints/{complaint.id}/close/", {"root_cause": "Duplicate charge, refunded."}, format="json")

    assert response.status_code == 200
    complaint.refresh_from_db()
    assert complaint.status == Complaint.Status.CLOSED
    assert complaint.root_cause == "Duplicate charge, refunded."
    assert complaint.closed_at is not None


@pytest.mark.django_db
def test_complaint_api_delete(auth_client, hospital, patient):
    complaint = Complaint.objects.create(hospital=hospital, patient=patient, description="X")
    response = auth_client.delete(f"/api/v1/complaints/{complaint.id}/")
    assert response.status_code == 204
    assert not Complaint.objects.filter(pk=complaint.id).exists()


@pytest.mark.django_db
def test_complaint_isolation(auth_client, other_hospital, other_patient):
    theirs = Complaint.objects.create(hospital=other_hospital, patient=other_patient, description="Theirs")
    assert auth_client.get(f"/api/v1/complaints/{theirs.id}/").status_code == 404
    assert auth_client.post(f"/api/v1/complaints/{theirs.id}/close/").status_code == 404
    assert auth_client.delete(f"/api/v1/complaints/{theirs.id}/").status_code == 404


# --- ServiceRecoveryTask: the sla_due_at read-only/required gotcha ---------

@pytest.mark.django_db
def test_service_recovery_task_model_create_requires_sla_due_at(hospital, patient, feedback_request):
    # score=9 (promoter range) deliberately avoids apps.feedback.signals'
    # on_nps_response handler, which auto-creates a ServiceRecoveryTask for
    # DETRACTOR-category responses only — a detractor score here would
    # collide with the one this test creates explicitly (OneToOneField).
    nps = NPSResponse.objects.create(hospital=hospital, feedback_request=feedback_request, patient=patient, score=9)
    task = ServiceRecoveryTask.objects.create(hospital=hospital, nps_response=nps, sla_due_at=timezone.now() + datetime.timedelta(hours=4))
    assert task.status == ServiceRecoveryTask.Status.PENDING


@pytest.mark.django_db
def test_service_recovery_task_api_create_without_sla_due_at_now_returns_400(auth_client, hospital, patient, feedback_request):
    """Was a real bug: `sla_due_at` is a required, non-nullable model
    field with no default, but the serializer used to mark it read_only —
    silently dropped from validated_data, so a POST with just
    `nps_response` passed serializer validation and then crashed with an
    unhandled IntegrityError/500 at the database layer instead of a normal
    400. Fixed by making the field writable (not read-only) so DRF's own
    required-field validation catches this properly."""
    nps = NPSResponse.objects.create(hospital=hospital, feedback_request=feedback_request, patient=patient, score=9)

    response = auth_client.post("/api/v1/service-recovery-tasks/", {"nps_response": nps.id}, format="json")

    assert response.status_code == 400
    assert "sla_due_at" in response.data


@pytest.mark.django_db
def test_service_recovery_task_api_create_with_sla_due_at_now_actually_works(auth_client, hospital, patient, feedback_request):
    """Same fix, the positive case: previously this endpoint could never
    successfully create a row via the API at all, regardless of what was
    posted — now a caller who does supply sla_due_at gets a working
    create, not just a differently-worded failure."""
    nps = NPSResponse.objects.create(hospital=hospital, feedback_request=feedback_request, patient=patient, score=9)
    due_at = (timezone.now() + datetime.timedelta(hours=4)).isoformat()

    response = auth_client.post("/api/v1/service-recovery-tasks/", {"nps_response": nps.id, "sla_due_at": due_at}, format="json")

    assert response.status_code == 201
    created = ServiceRecoveryTask.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
    assert created.sla_due_at is not None


@pytest.mark.django_db
def test_service_recovery_task_resolve_action(auth_client, hospital, patient, feedback_request):
    nps = NPSResponse.objects.create(hospital=hospital, feedback_request=feedback_request, patient=patient, score=9)
    task = ServiceRecoveryTask.objects.create(hospital=hospital, nps_response=nps, sla_due_at=timezone.now() + datetime.timedelta(hours=4))

    response = auth_client.post(f"/api/v1/service-recovery-tasks/{task.id}/resolve/", {"resolution_notes": "Called and apologised."}, format="json")

    assert response.status_code == 200
    task.refresh_from_db()
    assert task.status == ServiceRecoveryTask.Status.RESOLVED
    assert task.resolution_notes == "Called and apologised."
    assert task.resolved_at is not None


@pytest.mark.django_db
def test_service_recovery_task_is_auto_created_for_a_detractor_response(hospital, patient, feedback_request):
    """The one production path that successfully creates this model — via
    the on_nps_response signal, not the API."""
    nps = NPSResponse.objects.create(hospital=hospital, feedback_request=feedback_request, patient=patient, score=2)
    task = ServiceRecoveryTask.objects.get(nps_response=nps)
    assert task.status == ServiceRecoveryTask.Status.PENDING
    assert task.sla_due_at is not None


@pytest.mark.django_db
def test_service_recovery_task_isolation(auth_client, other_hospital, other_patient):
    their_request = FeedbackRequest.objects.create(hospital=other_hospital, patient=other_patient)
    their_nps = NPSResponse.objects.create(hospital=other_hospital, feedback_request=their_request, patient=other_patient, score=9)
    theirs = ServiceRecoveryTask.objects.create(hospital=other_hospital, nps_response=their_nps, sla_due_at=timezone.now() + datetime.timedelta(hours=4))

    assert auth_client.get(f"/api/v1/service-recovery-tasks/{theirs.id}/").status_code == 404
    assert auth_client.post(f"/api/v1/service-recovery-tasks/{theirs.id}/resolve/").status_code == 404


# --- SubmitFeedbackView: public, token-based --------------------------------

@pytest.mark.django_db
def test_submit_feedback_promoter_gets_a_google_review_link(api_client, feedback_request):
    response = api_client.post(f"/api/v1/feedback/{feedback_request.token}/", {"score": 10, "comment": "Excellent care!"}, format="json")

    assert response.status_code == 201
    assert "google_review_url" in response.data
    assert NPSResponse.objects.filter(feedback_request=feedback_request, score=10).exists()


@pytest.mark.django_db
def test_submit_feedback_detractor_creates_a_service_recovery_task(api_client, feedback_request):
    response = api_client.post(f"/api/v1/feedback/{feedback_request.token}/", {"score": 2, "comment": "Long wait."}, format="json")

    assert response.status_code == 201
    assert "google_review_url" not in response.data
    nps = NPSResponse.objects.get(feedback_request=feedback_request)
    assert nps.category == NPSResponse.Category.DETRACTOR
    task = ServiceRecoveryTask.objects.get(nps_response=nps)
    assert task.sla_due_at is not None


@pytest.mark.django_db
def test_submit_feedback_marks_the_feedback_request_responded(api_client, feedback_request):
    api_client.post(f"/api/v1/feedback/{feedback_request.token}/", {"score": 8}, format="json")
    feedback_request.refresh_from_db()
    assert feedback_request.status == FeedbackRequest.Status.RESPONDED


@pytest.mark.django_db
def test_submit_feedback_twice_returns_409(api_client, feedback_request):
    first = api_client.post(f"/api/v1/feedback/{feedback_request.token}/", {"score": 8}, format="json")
    assert first.status_code == 201

    second = api_client.post(f"/api/v1/feedback/{feedback_request.token}/", {"score": 5}, format="json")
    assert second.status_code == 409


@pytest.mark.django_db
def test_submit_feedback_404s_for_an_unknown_token(api_client):
    response = api_client.post("/api/v1/feedback/not-a-real-token/", {"score": 8}, format="json")
    assert response.status_code == 404


@pytest.mark.django_db
def test_submit_feedback_score_out_of_range_returns_400(api_client, feedback_request):
    response = api_client.post(f"/api/v1/feedback/{feedback_request.token}/", {"score": 11}, format="json")
    assert response.status_code == 400
