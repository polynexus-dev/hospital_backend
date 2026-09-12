from datetime import timedelta

import pytest
from django.utils import timezone

from apps.patients.models import Patient

from .models import DataRightsRequest, GrievanceTicket, Nominee


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Asha", last_name="Patil", mobile="9822011111")


# --- DataRightsRequest: SLA, verify, export, complete, reject --------------

@pytest.mark.django_db
def test_creating_a_request_sets_sla_due_at_automatically(settings, hospital, patient):
    settings.DATA_RIGHTS_REQUEST_SLA_DAYS = 30
    before = timezone.now()
    req = DataRightsRequest.objects.create(hospital=hospital, patient=patient, request_type=DataRightsRequest.RequestType.ACCESS)
    assert req.sla_due_at is not None
    assert req.sla_due_at >= before + timedelta(days=29)
    assert req.sla_due_at <= before + timedelta(days=31)


@pytest.mark.django_db
def test_verify_stamps_verifier_and_moves_to_verified(auth_client, hospital, patient, user):
    req = DataRightsRequest.objects.create(hospital=hospital, patient=patient, request_type=DataRightsRequest.RequestType.ACCESS)

    response = auth_client.post(f"/api/v1/privacy/data-rights-requests/{req.id}/verify/")

    assert response.status_code == 200
    req.refresh_from_db()
    assert req.status == DataRightsRequest.Status.VERIFIED
    assert req.verified_by_id == user.id
    assert req.verified_at is not None


@pytest.mark.django_db
def test_export_rejected_before_verification(auth_client, hospital, patient):
    req = DataRightsRequest.objects.create(hospital=hospital, patient=patient, request_type=DataRightsRequest.RequestType.ACCESS)
    response = auth_client.get(f"/api/v1/privacy/data-rights-requests/{req.id}/export/")
    assert response.status_code == 400


@pytest.mark.django_db
def test_export_returns_patient_data_bundle_once_verified(auth_client, hospital, patient):
    from apps.patients.models import Document, Prescription

    Document.objects.create(hospital=hospital, patient=patient, title="Blood test report", notes="Normal CBC")
    Prescription.objects.create(hospital=hospital, patient=patient, diagnosis="Viral fever")

    req = DataRightsRequest.objects.create(hospital=hospital, patient=patient, request_type=DataRightsRequest.RequestType.ACCESS)
    auth_client.post(f"/api/v1/privacy/data-rights-requests/{req.id}/verify/")

    response = auth_client.get(f"/api/v1/privacy/data-rights-requests/{req.id}/export/")

    assert response.status_code == 200
    assert response.data["patient"]["mobile"] == "9822011111"
    assert len(response.data["documents"]) == 1
    assert response.data["documents"][0]["notes"] == "Normal CBC"
    assert len(response.data["prescriptions"]) == 1
    assert response.data["prescriptions"][0]["diagnosis"] == "Viral fever"


@pytest.mark.django_db
def test_export_rejects_a_non_access_request(auth_client, hospital, patient):
    req = DataRightsRequest.objects.create(hospital=hospital, patient=patient, request_type=DataRightsRequest.RequestType.CORRECTION)
    auth_client.post(f"/api/v1/privacy/data-rights-requests/{req.id}/verify/")
    response = auth_client.get(f"/api/v1/privacy/data-rights-requests/{req.id}/export/")
    assert response.status_code == 400


@pytest.mark.django_db
def test_complete_rejected_before_verification(auth_client, hospital, patient):
    req = DataRightsRequest.objects.create(hospital=hospital, patient=patient, request_type=DataRightsRequest.RequestType.CORRECTION)
    response = auth_client.post(f"/api/v1/privacy/data-rights-requests/{req.id}/complete/", {"resolution_notes": "done"}, format="json")
    assert response.status_code == 400
    req.refresh_from_db()
    assert req.status == DataRightsRequest.Status.SUBMITTED


@pytest.mark.django_db
def test_complete_on_an_erasure_request_soft_deletes_the_patient(auth_client, hospital, patient, user):
    req = DataRightsRequest.objects.create(hospital=hospital, patient=patient, request_type=DataRightsRequest.RequestType.ERASURE)
    auth_client.post(f"/api/v1/privacy/data-rights-requests/{req.id}/verify/")

    response = auth_client.post(f"/api/v1/privacy/data-rights-requests/{req.id}/complete/", {"resolution_notes": "Verified and erased."}, format="json")

    assert response.status_code == 200
    req.refresh_from_db()
    assert req.status == DataRightsRequest.Status.COMPLETED
    assert req.handled_by_id == user.id
    assert req.resolution_notes == "Verified and erased."

    assert not Patient.objects.filter(pk=patient.pk).exists()
    deleted = Patient.objects.all_with_deleted().get(pk=patient.pk)
    assert deleted.is_deleted is True
    assert deleted.delete_reason == f"DPDP erasure request #{req.pk}"


@pytest.mark.django_db
def test_reject_stamps_handler_and_resolution(auth_client, hospital, patient, user):
    req = DataRightsRequest.objects.create(hospital=hospital, patient=patient, request_type=DataRightsRequest.RequestType.ERASURE)

    response = auth_client.post(f"/api/v1/privacy/data-rights-requests/{req.id}/reject/", {"resolution_notes": "Could not verify identity."}, format="json")

    assert response.status_code == 200
    req.refresh_from_db()
    assert req.status == DataRightsRequest.Status.REJECTED
    assert req.handled_by_id == user.id
    assert req.resolution_notes == "Could not verify identity."
    assert Patient.objects.filter(pk=patient.pk).exists()  # rejecting must not touch the patient record


@pytest.mark.django_db
def test_data_rights_request_isolation(auth_client, other_hospital):
    other_patient = Patient.objects.create(hospital=other_hospital, first_name="Theirs", mobile="9000000099")
    theirs = DataRightsRequest.objects.create(hospital=other_hospital, patient=other_patient, request_type=DataRightsRequest.RequestType.ACCESS)
    assert auth_client.get(f"/api/v1/privacy/data-rights-requests/{theirs.id}/").status_code == 404


# --- GrievanceTicket: SLA, resolve -------------------------------------------

@pytest.mark.django_db
def test_grievance_ticket_sets_sla_due_at_automatically(settings, hospital):
    settings.GRIEVANCE_SLA_DAYS = 30
    before = timezone.now()
    ticket = GrievanceTicket.objects.create(hospital=hospital, subject="Long wait time", description="Waited 2 hours past appointment slot.")
    assert ticket.sla_due_at >= before + timedelta(days=29)


@pytest.mark.django_db
def test_grievance_resolve_action(auth_client, hospital):
    ticket = GrievanceTicket.objects.create(hospital=hospital, subject="Billing error", description="Charged twice for the same consultation.")

    response = auth_client.post(f"/api/v1/privacy/grievances/{ticket.id}/resolve/", {"resolution": "Refunded the duplicate charge."}, format="json")

    assert response.status_code == 200
    ticket.refresh_from_db()
    assert ticket.status == GrievanceTicket.Status.RESOLVED
    assert ticket.resolution == "Refunded the duplicate charge."
    assert ticket.resolved_at is not None


# --- Nominee: CRUD, verify, soft-delete --------------------------------------

@pytest.mark.django_db
def test_nominee_create_and_verify(auth_client, hospital, patient, user):
    create = auth_client.post("/api/v1/privacy/nominees/", {
        "patient": str(patient.id), "name": "Rekha Patil", "relationship": "daughter", "phone": "9876543210",
    }, format="json")
    assert create.status_code == 201
    nominee_id = create.data["id"]

    verify = auth_client.post(f"/api/v1/privacy/nominees/{nominee_id}/verify/")
    assert verify.status_code == 200
    nominee = Nominee.objects.get(pk=nominee_id)
    assert nominee.verified_by_id == user.id
    assert nominee.verified_at is not None


@pytest.mark.django_db
def test_nominee_delete_is_soft(auth_client, hospital, patient):
    nominee = Nominee.objects.create(hospital=hospital, patient=patient, name="Rekha", relationship="daughter")

    response = auth_client.delete(f"/api/v1/privacy/nominees/{nominee.id}/")

    assert response.status_code == 204
    assert not Nominee.objects.filter(pk=nominee.pk).exists()
    assert Nominee.objects.all_with_deleted().get(pk=nominee.pk).is_deleted is True


# --- RBAC: privacy is owner/admin/hospital_administrator only --------------

@pytest.mark.django_db
def test_restricted_role_cannot_read_any_privacy_endpoint(restricted_client, hospital, patient):
    """apps.accounts.permission_templates.PERMISSION_TEMPLATES only ever
    lists "privacy" under FULL_ACCESS_APPS (owner/admin/
    hospital_administrator) — every other template, including Telephony
    Operator here, omits it entirely. RoleBasedModelPermissions alone
    doesn't enforce that for list/retrieve, so DataRightsRequestViewSet/
    GrievanceTicketViewSet/NomineeViewSet add RequiresViewPermission."""
    data_rights_request = DataRightsRequest.objects.create(hospital=hospital, patient=patient, request_type=DataRightsRequest.RequestType.ACCESS)
    grievance = GrievanceTicket.objects.create(hospital=hospital, subject="Billing error", description="Charged twice.")
    nominee = Nominee.objects.create(hospital=hospital, patient=patient, name="Rekha", relationship="daughter")

    assert restricted_client.get("/api/v1/privacy/data-rights-requests/").status_code == 403
    assert restricted_client.get(f"/api/v1/privacy/data-rights-requests/{data_rights_request.id}/").status_code == 403
    assert restricted_client.get("/api/v1/privacy/grievances/").status_code == 403
    assert restricted_client.get(f"/api/v1/privacy/grievances/{grievance.id}/").status_code == 403
    assert restricted_client.get("/api/v1/privacy/nominees/").status_code == 403
    assert restricted_client.get(f"/api/v1/privacy/nominees/{nominee.id}/").status_code == 403
