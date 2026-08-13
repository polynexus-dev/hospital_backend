import datetime
from contextlib import suppress

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.patients.models import Document, Patient, Prescription, TimelineEvent, record_timeline_event


@pytest.fixture
def patient(hospital):
    p = Patient.objects.create(hospital=hospital, first_name="Asha", last_name="Patil", mobile="9844000001")
    yield p
    with suppress(Exception):
        p.delete()


@pytest.fixture
def other_patient(other_hospital):
    p = Patient.objects.create(hospital=other_hospital, first_name="Theirs", mobile="9844000002")
    yield p
    with suppress(Exception):
        p.delete()


# --- Patient: model CRUD -----------------------------------------------

@pytest.mark.django_db
def test_patient_model_create_with_required_fields_only(hospital):
    p = Patient.objects.create(hospital=hospital, first_name="Rekha", mobile="9844000003")
    assert p.preferred_language == "mr"
    assert p.is_active is True


@pytest.mark.django_db
def test_patient_model_full_name_property(hospital):
    p = Patient.objects.create(hospital=hospital, first_name="Rekha", last_name="Joshi", mobile="9844000004")
    assert p.full_name == "Rekha Joshi"


@pytest.mark.django_db
def test_patient_model_no_db_level_mobile_uniqueness(hospital):
    """`Patient.mobile` has an index but no uniqueness constraint —
    duplicate mobiles across different patient rows are allowed at the DB
    level (real family members sharing one phone, etc.)."""
    Patient.objects.create(hospital=hospital, first_name="A", mobile="9844000005")
    second = Patient.objects.create(hospital=hospital, first_name="B", mobile="9844000005")
    assert second.pk is not None


@pytest.mark.django_db
def test_patient_model_update_and_delete(patient):
    patient.city = "Pune"
    patient.save(update_fields=["city"])
    patient.refresh_from_db()
    assert patient.city == "Pune"

    patient_id = patient.id
    patient.delete()
    assert not Patient.objects.filter(pk=patient_id).exists()


# --- Patient: API CRUD --------------------------------------------------

@pytest.mark.django_db
def test_patient_api_create_requires_first_name_and_mobile(auth_client, hospital):
    response = auth_client.post("/api/v1/patients/", {"first_name": "New Patient", "mobile": "9844000006"}, format="json")
    assert response.status_code == 201
    created = Patient.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id


@pytest.mark.django_db
def test_patient_api_update_and_delete(auth_client, patient):
    update = auth_client.patch(f"/api/v1/patients/{patient.id}/", {"city": "Mumbai"}, format="json")
    assert update.status_code == 200
    patient.refresh_from_db()
    assert patient.city == "Mumbai"

    delete = auth_client.delete(f"/api/v1/patients/{patient.id}/")
    assert delete.status_code == 204
    assert not Patient.objects.filter(pk=patient.id).exists()


@pytest.mark.django_db
def test_patient_isolation(auth_client, other_patient):
    assert auth_client.get(f"/api/v1/patients/{other_patient.id}/").status_code == 404
    assert auth_client.patch(f"/api/v1/patients/{other_patient.id}/", {"city": "X"}, format="json").status_code == 404
    assert auth_client.delete(f"/api/v1/patients/{other_patient.id}/").status_code == 404
    ids = {row["id"] for row in auth_client.get("/api/v1/patients/").data["results"]}
    assert other_patient.id not in ids


# --- lookup action: security regression -------------------------------
#
# lookup() used to call Patient.objects.filter(...) directly instead of
# self.get_queryset() — since TenantManager only auto-scopes via a
# contextvar that's never populated for this app's JWT-authenticated
# requests, that meant ANY hospital's front-desk user could resolve ANY
# OTHER hospital's patient by mobile number (verified empirically before
# the fix: a Hospital A user's lookup call returned a Hospital B patient's
# full match). Fixed by routing through self.get_queryset() like every
# other action. These tests pin that down.

@pytest.mark.django_db
def test_lookup_finds_a_patient_in_the_callers_own_hospital(auth_client, patient):
    response = auth_client.get(f"/api/v1/patients/lookup/?mobile={patient.mobile}")
    assert response.status_code == 200
    ids = {row["id"] for row in response.data}
    assert patient.id in ids


@pytest.mark.django_db
def test_lookup_does_not_leak_another_hospitals_patient(auth_client, other_patient):
    response = auth_client.get(f"/api/v1/patients/lookup/?mobile={other_patient.mobile}")
    assert response.status_code == 200
    assert response.data == []


@pytest.mark.django_db
def test_lookup_matches_on_alternate_mobile_too(auth_client, hospital):
    p = Patient.objects.create(hospital=hospital, first_name="X", mobile="9844000007", alternate_mobile="9844000008")
    response = auth_client.get("/api/v1/patients/lookup/?mobile=9844000008")
    ids = {row["id"] for row in response.data}
    assert p.id in ids


@pytest.mark.django_db
def test_lookup_without_mobile_param_returns_400(auth_client):
    response = auth_client.get("/api/v1/patients/lookup/")
    assert response.status_code == 400


# --- timeline action ------------------------------------------------------

@pytest.mark.django_db
def test_timeline_action_returns_events_for_the_patient(auth_client, patient):
    record_timeline_event(patient=patient, event_type="call", summary="Inbound enquiry call", occurred_at=datetime.datetime.now(datetime.timezone.utc))

    response = auth_client.get(f"/api/v1/patients/{patient.id}/timeline/")

    assert response.status_code == 200
    assert len(response.data) == 1
    assert response.data[0]["summary"] == "Inbound enquiry call"


@pytest.mark.django_db
def test_timeline_action_404s_for_another_hospitals_patient(auth_client, other_patient):
    response = auth_client.get(f"/api/v1/patients/{other_patient.id}/timeline/")
    assert response.status_code == 404


@pytest.mark.django_db
def test_timeline_event_has_no_direct_create_endpoint():
    """TimelineEventSerializer is fully read_only_fields=fields and isn't
    exposed as its own viewset — rows only ever come from
    record_timeline_event(), called from other apps' signal handlers."""
    from apps.patients.urls import urlpatterns
    paths = [str(p.pattern) for p in urlpatterns]
    assert not any("timeline-event" in p for p in paths)


# --- Document: multipart upload, required file, isolation -----------------

@pytest.mark.django_db
def test_document_api_create_requires_multipart_file(auth_client, hospital, user, patient):
    upload = SimpleUploadedFile("report.pdf", b"%PDF-1.4 fake content", content_type="application/pdf")

    response = auth_client.post("/api/v1/documents/", {"patient": patient.id, "category": "report", "file": upload}, format="multipart")

    assert response.status_code == 201
    created = Document.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
    assert created.uploaded_by_id == user.id


@pytest.mark.django_db
def test_document_api_create_without_file_returns_400(auth_client, patient):
    response = auth_client.post("/api/v1/documents/", {"patient": patient.id}, format="multipart")
    assert response.status_code == 400
    assert "file" in response.data


@pytest.mark.django_db
def test_document_isolation(auth_client, other_hospital, other_patient):
    upload = SimpleUploadedFile("secret.pdf", b"secret", content_type="application/pdf")
    theirs = Document.objects.create(hospital=other_hospital, patient=other_patient, file=upload)

    assert auth_client.get(f"/api/v1/documents/{theirs.id}/").status_code == 404
    assert auth_client.delete(f"/api/v1/documents/{theirs.id}/").status_code == 404


@pytest.mark.django_db
def test_document_api_delete(auth_client, hospital, patient):
    upload = SimpleUploadedFile("x.pdf", b"x", content_type="application/pdf")
    document = Document.objects.create(hospital=hospital, patient=patient, file=upload)

    response = auth_client.delete(f"/api/v1/documents/{document.id}/")

    assert response.status_code == 204
    assert not Document.objects.filter(pk=document.id).exists()


# --- Prescription: doctor auto-stamped to request.user, isolation ---------

@pytest.mark.django_db
def test_prescription_api_create_stamps_doctor_to_requesting_user_even_if_spoofed(auth_client, hospital, user, other_user, patient):
    """`doctor` on Prescription points at settings.AUTH_USER_MODEL (the
    staff account that wrote it), not apps.appointments.models.Doctor — and
    perform_create always overrides it to request.user regardless of what's
    posted, so a spoofed `doctor` in the body is silently ignored."""
    response = auth_client.post("/api/v1/prescriptions/", {"patient": patient.id, "diagnosis": "Hypertension", "doctor": other_user.id}, format="json")

    assert response.status_code == 201
    created = Prescription.objects.get(pk=response.data["id"])
    assert created.doctor_id == user.id
    assert created.hospital_id == hospital.id


@pytest.mark.django_db
def test_prescription_api_create_without_diagnosis_returns_400(auth_client, patient):
    response = auth_client.post("/api/v1/prescriptions/", {"patient": patient.id}, format="json")
    assert response.status_code == 400
    assert "diagnosis" in response.data


@pytest.mark.django_db
def test_prescription_api_update_and_delete(auth_client, hospital, user, patient):
    prescription = Prescription.objects.create(hospital=hospital, patient=patient, doctor=user, diagnosis="Initial")

    update = auth_client.patch(f"/api/v1/prescriptions/{prescription.id}/", {"diagnosis": "Revised diagnosis"}, format="json")
    assert update.status_code == 200
    prescription.refresh_from_db()
    assert prescription.diagnosis == "Revised diagnosis"

    delete = auth_client.delete(f"/api/v1/prescriptions/{prescription.id}/")
    assert delete.status_code == 204
    assert not Prescription.objects.filter(pk=prescription.id).exists()


@pytest.mark.django_db
def test_prescription_isolation(auth_client, other_hospital, other_user, other_patient):
    theirs = Prescription.objects.create(hospital=other_hospital, patient=other_patient, doctor=other_user, diagnosis="Theirs")
    assert auth_client.get(f"/api/v1/prescriptions/{theirs.id}/").status_code == 404
    assert auth_client.delete(f"/api/v1/prescriptions/{theirs.id}/").status_code == 404
