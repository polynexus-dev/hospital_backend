import datetime

import pytest

from apps.accounts.models import Role, User, assign_role
from apps.appointments.models import Doctor, Slot
from apps.appointments.services import book_appointment, check_in
from apps.opd.models import ClinicalNote, Diagnosis, Encounter, InvestigationOrder, VitalsReading
from apps.patients.models import Patient


@pytest.fixture
def doctor(hospital, department):
    return Doctor.objects.create(hospital=hospital, department=department, name="Mehta")


@pytest.fixture
def slot(hospital, doctor):
    return Slot.objects.create(
        hospital=hospital, doctor=doctor,
        date=datetime.date.today() + datetime.timedelta(days=1),
        start_time=datetime.time(10, 0), end_time=datetime.time(10, 15),
    )


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Sunita", mobile="9988776655")


@pytest.fixture
def checked_in_appointment(hospital, patient, slot):
    appointment = book_appointment(patient=patient, slot=slot)
    return check_in(appointment)


@pytest.fixture
def encounter(checked_in_appointment):
    return Encounter.objects.get(appointment=checked_in_appointment)


@pytest.fixture
def doctor_user(hospital, department, doctor):
    """A logged-in user linked to the Doctor directory entry via
    Doctor.user — this is what assignment_scope_field="doctor__user"
    matches against for assigned_only-scoped roles."""
    role = Role.objects.create(hospital=hospital, department=department, name="Dr. Mehta's Role", template=Role.Template.DOCTOR, data_scope=Role.DataScope.ASSIGNED_ONLY)
    user = User.objects.create_user(email="dr-mehta@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(user, role)
    doctor.user = user
    doctor.save(update_fields=["user"])
    return user


@pytest.fixture
def doctor_client(api_client, doctor_user):
    api_client.force_authenticate(user=doctor_user)
    yield api_client
    api_client.force_authenticate(user=None)


@pytest.fixture
def receptionist_client(api_client, hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Receptionist", template=Role.Template.RECEPTIONIST)
    receptionist = User.objects.create_user(email="reception@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(receptionist, role)
    api_client.force_authenticate(user=receptionist)
    yield api_client
    api_client.force_authenticate(user=None)


# --- Encounter creation on check-in (docs/erp/05-integration-architecture.md) ---

@pytest.mark.django_db
def test_checking_in_an_appointment_creates_exactly_one_encounter(hospital, patient, slot):
    appointment = book_appointment(patient=patient, slot=slot)
    assert not Encounter.objects.filter(appointment=appointment).exists()

    check_in(appointment)

    assert Encounter.objects.filter(appointment=appointment).count() == 1
    created = Encounter.objects.get(appointment=appointment)
    assert created.patient_id == patient.id
    assert created.doctor_id == appointment.doctor_id
    assert created.hospital_id == hospital.id


@pytest.mark.django_db
def test_checking_in_twice_does_not_create_a_second_encounter(hospital, patient, slot):
    """check_in() can run more than once for the same appointment in edge
    cases — the signal handler must be idempotent, not raise on a
    duplicate OneToOneField."""
    appointment = book_appointment(patient=patient, slot=slot)
    check_in(appointment)
    check_in(appointment)

    assert Encounter.objects.filter(appointment=appointment).count() == 1


# --- VitalsReading -----------------------------------------------------

@pytest.mark.django_db
def test_vitals_reading_api_create_stamps_recorded_by(doctor_client, doctor_user, encounter):
    response = doctor_client.post("/api/v1/opd/vitals/", {
        "encounter": encounter.id, "bp_systolic": 120, "bp_diastolic": 80, "pulse": 72,
    }, format="json")
    assert response.status_code == 201
    created = VitalsReading.objects.get(pk=response.data["id"])
    assert created.recorded_by_id == doctor_user.id


# --- ClinicalNote: creation, finalize-locking, RBAC ---------------------

@pytest.mark.django_db
def test_clinical_note_api_create_stamps_doctor_from_encounter(doctor_client, doctor, encounter):
    response = doctor_client.post("/api/v1/opd/clinical-notes/", {
        "encounter": encounter.id, "chief_complaints": "Fever, cough",
    }, format="json")
    assert response.status_code == 201
    created = ClinicalNote.objects.get(pk=response.data["id"])
    assert created.doctor_id == doctor.id


@pytest.mark.django_db
def test_clinical_note_finalize_locks_it_against_further_edits(doctor_client, encounter):
    create = doctor_client.post("/api/v1/opd/clinical-notes/", {
        "encounter": encounter.id, "chief_complaints": "Fever",
    }, format="json")
    note_id = create.data["id"]

    finalize = doctor_client.post(f"/api/v1/opd/clinical-notes/{note_id}/finalize/")
    assert finalize.status_code == 200
    assert finalize.data["finalized_at"] is not None

    note = ClinicalNote.objects.get(pk=note_id)
    note.chief_complaints = "Tampered"
    with pytest.raises(ValueError):
        note.save()

    note.refresh_from_db()
    assert note.chief_complaints == "Fever"


@pytest.mark.django_db
def test_clinical_note_finalize_requires_the_finalize_permission(hospital, department, encounter):
    """A role with opd add/change but no explicit finalize_clinicalnote
    grant can create a note but not finalize it — proves
    ActionPermissionRequired actually gates the action, not just the
    generic add/change/view verbs RoleBasedModelPermissions covers."""
    from rest_framework.test import APIClient

    role = Role.objects.create(hospital=hospital, department=department, name="Junior Doctor")  # template="" (blank) — hand-built permission set below
    from django.contrib.auth.models import Permission
    role.group.permissions.add(*Permission.objects.filter(codename__in=[
        "add_clinicalnote", "view_clinicalnote", "change_clinicalnote", "access_clinical_detail",
    ]))
    user = User.objects.create_user(email="junior@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(user, role)
    client = APIClient()
    client.force_authenticate(user=user)

    create = client.post("/api/v1/opd/clinical-notes/", {"encounter": encounter.id, "chief_complaints": "Fever"}, format="json")
    assert create.status_code == 201

    finalize = client.post(f"/api/v1/opd/clinical-notes/{create.data['id']}/finalize/")
    assert finalize.status_code == 403


# --- Diagnosis -----------------------------------------------------------

@pytest.mark.django_db
def test_diagnosis_api_create_stamps_created_by(doctor_client, doctor_user, encounter):
    response = doctor_client.post("/api/v1/opd/diagnoses/", {
        "encounter": encounter.id, "description": "Viral fever",
    }, format="json")
    assert response.status_code == 201
    created = Diagnosis.objects.get(pk=response.data["id"])
    assert created.created_by_id == doctor_user.id
    assert created.diagnosis_type == Diagnosis.DiagnosisType.PROVISIONAL


@pytest.mark.django_db
def test_diagnosis_finalize_locks_it(doctor_client, encounter):
    create = doctor_client.post("/api/v1/opd/diagnoses/", {"encounter": encounter.id, "description": "Viral fever"}, format="json")
    diagnosis_id = create.data["id"]

    finalize = doctor_client.post(f"/api/v1/opd/diagnoses/{diagnosis_id}/finalize/")
    assert finalize.status_code == 200

    diagnosis = Diagnosis.objects.get(pk=diagnosis_id)
    diagnosis.description = "Tampered"
    with pytest.raises(ValueError):
        diagnosis.save()


# --- Amendment: the correction path after finalize (docs/erp/07-audit-and-security.md §2b) ---

@pytest.mark.django_db
def test_correcting_a_finalized_diagnosis_goes_through_amendment_not_a_silent_edit(hospital, doctor_user, encounter):
    from django.contrib.contenttypes.models import ContentType

    from apps.core.models import Amendment

    diagnosis = Diagnosis.objects.create(hospital=hospital, encounter=encounter, description="Viral fever", created_by=doctor_user)
    diagnosis.finalize(doctor_user)

    amendment = Amendment.objects.create(
        hospital=hospital,
        content_type=ContentType.objects.get_for_model(diagnosis),
        object_id=str(diagnosis.pk),
        field_name="description",
        previous_value="Viral fever",
        corrected_value="Dengue fever",
        reason="Confirmed by NS1 antigen test after initial provisional diagnosis.",
        amended_by=doctor_user,
    )

    assert amendment.content_object == diagnosis
    diagnosis.refresh_from_db()
    assert diagnosis.description == "Viral fever"  # original untouched — the amendment is the correction record


# --- RBAC: assignment_scope_field + RequiresClinicalDetailPermission ----

@pytest.mark.django_db
def test_a_doctor_with_assigned_only_scope_sees_only_their_own_encounters(hospital, department, doctor, doctor_user, encounter, patient):
    other_doctor = Doctor.objects.create(hospital=hospital, department=department, name="Iyer")
    other_slot = Slot.objects.create(hospital=hospital, doctor=other_doctor, date=datetime.date.today() + datetime.timedelta(days=1), start_time=datetime.time(11, 0), end_time=datetime.time(11, 15))
    other_appt = book_appointment(patient=patient, slot=other_slot)
    check_in(other_appt)

    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=doctor_user)

    response = client.get("/api/v1/opd/encounters/")
    ids = [e["id"] for e in response.data["results"]]
    assert encounter.id in ids
    assert Encounter.objects.get(appointment=other_appt).id not in ids


@pytest.mark.django_db
def test_receptionist_role_is_blocked_from_every_opd_endpoint(receptionist_client, encounter):
    assert receptionist_client.get("/api/v1/opd/encounters/").status_code == 403
    assert receptionist_client.get("/api/v1/opd/clinical-notes/").status_code == 403
    assert receptionist_client.get("/api/v1/opd/diagnoses/").status_code == 403
    assert receptionist_client.post("/api/v1/opd/vitals/", {"encounter": encounter.id}, format="json").status_code == 403


@pytest.mark.django_db
def test_receptionist_template_does_not_grant_access_clinical_detail(hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Receptionist", template=Role.Template.RECEPTIONIST)
    user = User.objects.create_user(email="reception-check@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(user, role)
    assert not user.has_perm("patients.access_clinical_detail")


@pytest.mark.django_db
def test_hospital_administrator_template_grants_facilities_but_not_clinical_detail(hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Hospital Admin", template=Role.Template.HOSPITAL_ADMINISTRATOR)
    user = User.objects.create_user(email="hosp-admin@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(user, role)
    assert user.has_perm("facilities.add_ward")
    assert not user.has_perm("patients.access_clinical_detail")


# --- Tenant isolation ----------------------------------------------------

@pytest.mark.django_db
def test_encounter_isolation(auth_client, other_hospital, other_department):
    other_doctor = Doctor.objects.create(hospital=other_hospital, department=other_department, name="Theirs")
    other_patient = Patient.objects.create(hospital=other_hospital, first_name="Theirs", mobile="9000000099")
    other_slot = Slot.objects.create(hospital=other_hospital, doctor=other_doctor, date=datetime.date.today() + datetime.timedelta(days=1), start_time=datetime.time(9, 0), end_time=datetime.time(9, 15))
    other_appt = book_appointment(patient=other_patient, slot=other_slot)
    check_in(other_appt)
    theirs = Encounter.objects.get(appointment=other_appt)

    assert auth_client.get(f"/api/v1/opd/encounters/{theirs.id}/").status_code == 404


@pytest.mark.django_db
def test_investigation_order_api_create(doctor_client, doctor_user, encounter):
    response = doctor_client.post("/api/v1/opd/investigation-orders/", {
        "encounter": encounter.id, "order_type": "lab", "description": "CBC",
    }, format="json")
    assert response.status_code == 201
    assert InvestigationOrder.objects.get(pk=response.data["id"]).created_by_id == doctor_user.id
