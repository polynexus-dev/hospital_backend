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


# --- Patient: national_id_number / insurance_policy_number are encrypted at rest ---

@pytest.mark.django_db
def test_national_id_and_insurance_policy_number_round_trip_through_the_orm(hospital):
    """apps.core.encryption.EncryptedTextField should be invisible from the
    ORM's point of view — what you write is what you read back."""
    p = Patient.objects.create(
        hospital=hospital, first_name="Encrypted", mobile="9844000099",
        national_id_number="1234-5678-9012", insurance_policy_number="POL-STAR-998877",
    )
    p.refresh_from_db()
    assert p.national_id_number == "1234-5678-9012"
    assert p.insurance_policy_number == "POL-STAR-998877"


@pytest.mark.django_db
def test_national_id_and_insurance_policy_number_are_ciphertext_in_the_database(hospital):
    """The whole point of C2's fix (see docs/SECURITY_COMPLIANCE.md) — a raw
    row read (backup, replica, dbshell) must not see plaintext PII."""
    from django.db import connection

    p = Patient.objects.create(
        hospital=hospital, first_name="Encrypted", mobile="9844000098",
        national_id_number="1234-5678-9012", insurance_policy_number="POL-STAR-998877",
    )
    with connection.cursor() as cur:
        cur.execute(
            "SELECT national_id_number, insurance_policy_number FROM patients_patient WHERE id = %s",
            [p.id],
        )
        raw_national_id, raw_policy_number = cur.fetchone()
    assert raw_national_id != "1234-5678-9012"
    assert raw_policy_number != "POL-STAR-998877"
    assert "1234-5678-9012" not in raw_national_id
    assert "POL-STAR-998877" not in raw_policy_number


@pytest.mark.django_db
def test_blank_national_id_and_insurance_policy_number_stay_blank(hospital):
    """Empty values shouldn't be encrypted into a non-empty ciphertext blob
    — that would make "no ID on file" indistinguishable from "has an ID"
    at the storage layer, and waste a Fernet token on nothing."""
    p = Patient.objects.create(hospital=hospital, first_name="NoId", mobile="9844000097")
    p.refresh_from_db()
    assert p.national_id_number == ""
    assert p.insurance_policy_number == ""


# --- Patient: UHID (docs/erp/02-domain-model.md) ---------------------------

@pytest.mark.django_db
def test_patient_gets_a_uhid_assigned_automatically_on_first_save(hospital):
    p = Patient.objects.create(hospital=hospital, first_name="Auto", mobile="9844000090")
    assert p.uhid
    assert p.uhid.startswith(hospital.slug.upper())


@pytest.mark.django_db
def test_patient_uhids_are_sequential_and_unique_within_a_hospital(hospital):
    first = Patient.objects.create(hospital=hospital, first_name="One", mobile="9844000091")
    second = Patient.objects.create(hospital=hospital, first_name="Two", mobile="9844000092")
    assert first.uhid != second.uhid
    first_seq = int(first.uhid.rsplit("-", 1)[1])
    second_seq = int(second.uhid.rsplit("-", 1)[1])
    assert second_seq == first_seq + 1


@pytest.mark.django_db
def test_patient_uhid_is_not_regenerated_on_update(hospital):
    p = Patient.objects.create(hospital=hospital, first_name="Stable", mobile="9844000093")
    original_uhid = p.uhid
    p.city = "Pune"
    p.save(update_fields=["city"])
    p.refresh_from_db()
    assert p.uhid == original_uhid


@pytest.mark.django_db
def test_patient_uhid_sequences_are_independent_per_hospital(hospital, other_hospital):
    """Two hospitals both minting their first UHID shouldn't collide just
    because they both start their sequence at 1 — the hospital slug
    prefix is what keeps them apart."""
    ours = Patient.objects.create(hospital=hospital, first_name="Ours", mobile="9844000094")
    theirs = Patient.objects.create(hospital=other_hospital, first_name="Theirs", mobile="9844000095")
    assert ours.uhid != theirs.uhid
    assert ours.uhid.startswith(hospital.slug.upper())
    assert theirs.uhid.startswith(other_hospital.slug.upper())


# --- Patient: field-level clinical-detail gating (docs/erp/03-rbac-and-roles.md §2c) ---

@pytest.mark.django_db
def test_a_role_without_access_clinical_detail_does_not_see_national_id_fields(api_client, hospital, department):
    """crm_executive is a CRM role template — it can see and edit a
    Patient row (front-desk/insurance-enquiry work) but should never see
    national_id_number through this endpoint."""
    from apps.accounts.models import Role, User, assign_role

    crm_role = Role.objects.create(hospital=hospital, department=department, name="CRM Exec", template=Role.Template.CRM_EXECUTIVE)
    crm_user = User.objects.create_user(email="crm@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(crm_user, crm_role)
    api_client.force_authenticate(user=crm_user)

    p = Patient.objects.create(hospital=hospital, first_name="Sensitive", mobile="9844000096", national_id_number="1234-5678-9012")

    response = api_client.get(f"/api/v1/patients/{p.id}/")
    assert response.status_code == 200
    assert "national_id_number" not in response.data
    assert "national_id_type" not in response.data
    assert response.data["insurance_provider"] == ""  # still present — CRM legitimately needs insurance fields


@pytest.mark.django_db
def test_a_role_with_access_clinical_detail_does_see_national_id_fields(auth_client, hospital):
    """auth_client's role uses the "admin" template, which the new
    access_clinical_detail "extra" grant now includes (see
    apps.accounts.permission_templates)."""
    p = Patient.objects.create(hospital=hospital, first_name="Sensitive", mobile="9844000089", national_id_number="1234-5678-9012")

    response = auth_client.get(f"/api/v1/patients/{p.id}/")

    assert response.status_code == 200
    assert response.data["national_id_number"] == "1234-5678-9012"


@pytest.mark.django_db
def test_prescriptions_are_blocked_entirely_for_a_role_without_access_clinical_detail(api_client, hospital, department, patient):
    from apps.accounts.models import Role, User, assign_role

    crm_role = Role.objects.create(hospital=hospital, department=department, name="CRM Exec", template=Role.Template.CRM_EXECUTIVE)
    crm_user = User.objects.create_user(email="crm2@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(crm_user, crm_role)
    api_client.force_authenticate(user=crm_user)

    response = api_client.get("/api/v1/prescriptions/")
    assert response.status_code == 403


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
def test_document_api_rejects_a_disallowed_file_extension(auth_client, patient):
    upload = SimpleUploadedFile("payload.exe", b"MZ fake executable content", content_type="application/octet-stream")

    response = auth_client.post("/api/v1/documents/", {"patient": patient.id, "category": "report", "file": upload}, format="multipart")

    assert response.status_code == 400
    assert "file" in response.data


@pytest.mark.django_db
def test_document_api_rejects_a_file_over_the_size_limit(auth_client, patient):
    from apps.patients.models import MAX_DOCUMENT_SIZE_BYTES

    oversized = SimpleUploadedFile("report.pdf", b"x" * (MAX_DOCUMENT_SIZE_BYTES + 1), content_type="application/pdf")

    response = auth_client.post("/api/v1/documents/", {"patient": patient.id, "category": "report", "file": oversized}, format="multipart")

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
