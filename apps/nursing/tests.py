import pytest

from apps.accounts.models import Role, User, assign_role
from apps.appointments.models import Doctor
from apps.facilities.models import Bed, Room, Ward
from apps.ipd.services import admit_patient
from apps.nursing.models import IntakeOutput, MedicationAdministration, NursingNote
from apps.patients.models import Patient


@pytest.fixture
def doctor(hospital, department):
    return Doctor.objects.create(hospital=hospital, department=department, name="Mehta")


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Sunita", mobile="9988776655")


@pytest.fixture
def bed(hospital):
    ward = Ward.objects.create(hospital=hospital, name="General Ward A")
    room = Room.objects.create(hospital=hospital, ward=ward, room_number="101")
    return Bed.objects.create(hospital=hospital, room=room, bed_number="A")


@pytest.fixture
def admission(hospital, patient, doctor, bed):
    return admit_patient(hospital=hospital, patient=patient, admitting_doctor=doctor, bed=bed)


@pytest.fixture
def nurse_user(hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Nurse", template=Role.Template.NURSE)
    user = User.objects.create_user(email="nurse@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(user, role)
    return user


@pytest.fixture
def nurse_client(api_client, nurse_user):
    api_client.force_authenticate(user=nurse_user)
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


# --- Generic-relation resolution via the admission-scoped serializer -----

@pytest.mark.django_db
def test_nursing_note_api_create_resolves_admission_to_the_generic_relation(nurse_client, nurse_user, admission):
    response = nurse_client.post("/api/v1/nursing/notes/", {"admission": admission.id, "note": "Patient resting comfortably."}, format="json")
    assert response.status_code == 201

    created = NursingNote.objects.get(pk=response.data["id"])
    assert created.content_object == admission
    assert created.nurse_id == nurse_user.id
    assert response.data["admission_id"] == str(admission.id)


@pytest.mark.django_db
def test_nursing_note_list_filters_by_admission(nurse_client, admission, hospital, patient, doctor, bed):
    nurse_client.post("/api/v1/nursing/notes/", {"admission": admission.id, "note": "First note"}, format="json")

    other_bed = Bed.objects.create(hospital=hospital, room=bed.room, bed_number="B")
    other_patient = Patient.objects.create(hospital=hospital, first_name="Other", mobile="9988776699")
    other_admission = admit_patient(hospital=hospital, patient=other_patient, admitting_doctor=doctor, bed=other_bed)
    nurse_client.post("/api/v1/nursing/notes/", {"admission": other_admission.id, "note": "Unrelated note"}, format="json")

    response = nurse_client.get(f"/api/v1/nursing/notes/?admission={admission.id}")
    notes = response.data["results"]
    assert len(notes) == 1
    assert notes[0]["note"] == "First note"


@pytest.mark.django_db
def test_nursing_note_create_rejects_another_hospitals_admission(nurse_client, other_hospital, other_department):
    """_AdmissionScopedSerializer.validate_admission — Admission.objects.
    all() (the field's queryset) is unscoped, so without this check a
    nurse could point a note at another hospital's admission by guessing
    its id; the note itself would still be stamped with the nurse's own
    hospital, but the generic relation would point cross-tenant."""
    other_doctor = Doctor.objects.create(hospital=other_hospital, department=other_department, name="Not Mehta")
    other_patient = Patient.objects.create(hospital=other_hospital, first_name="NotMine", mobile="9988776600")
    other_ward = Ward.objects.create(hospital=other_hospital, name="Ward B")
    other_room = Room.objects.create(hospital=other_hospital, ward=other_ward, room_number="201")
    other_bed = Bed.objects.create(hospital=other_hospital, room=other_room, bed_number="A")
    theirs = admit_patient(hospital=other_hospital, patient=other_patient, admitting_doctor=other_doctor, bed=other_bed)

    response = nurse_client.post("/api/v1/nursing/notes/", {"admission": theirs.id, "note": "Should be rejected"}, format="json")

    assert response.status_code == 400
    assert not NursingNote.objects.filter(content_type__model="admission", object_id=str(theirs.id)).exists()


@pytest.mark.django_db
def test_medication_administration_api_create(nurse_client, nurse_user, admission):
    response = nurse_client.post("/api/v1/nursing/medication-administrations/", {
        "admission": admission.id, "medication_name": "Paracetamol 500mg", "dose": "1 tablet",
    }, format="json")
    assert response.status_code == 201
    created = MedicationAdministration.objects.get(pk=response.data["id"])
    assert created.content_object == admission
    assert created.nurse_id == nurse_user.id


@pytest.mark.django_db
def test_intake_output_api_create(nurse_client, nurse_user, admission):
    response = nurse_client.post("/api/v1/nursing/intake-output/", {
        "admission": admission.id, "intake_ml": 500, "output_ml": 300,
    }, format="json")
    assert response.status_code == 201
    created = IntakeOutput.objects.get(pk=response.data["id"])
    assert created.content_object == admission
    assert created.recorded_by_id == nurse_user.id


# --- RBAC / isolation ------------------------------------------------------

@pytest.mark.django_db
def test_receptionist_role_is_blocked_from_every_nursing_endpoint(receptionist_client, admission):
    assert receptionist_client.get("/api/v1/nursing/notes/").status_code == 403
    assert receptionist_client.post("/api/v1/nursing/notes/", {"admission": admission.id, "note": "x"}, format="json").status_code == 403


@pytest.mark.django_db
def test_nursing_note_isolation(auth_client, other_hospital, other_department):
    other_doctor = Doctor.objects.create(hospital=other_hospital, department=other_department, name="Theirs")
    other_patient = Patient.objects.create(hospital=other_hospital, first_name="Theirs", mobile="9000000097")
    other_ward = Ward.objects.create(hospital=other_hospital, name="Ward")
    other_room = Room.objects.create(hospital=other_hospital, ward=other_ward, room_number="1")
    other_bed = Bed.objects.create(hospital=other_hospital, room=other_room, bed_number="A")
    other_admission = admit_patient(hospital=other_hospital, patient=other_patient, admitting_doctor=other_doctor, bed=other_bed)

    from apps.nursing.models import NursingNote as NursingNoteModel
    from django.contrib.contenttypes.models import ContentType
    theirs = NursingNoteModel.objects.create(
        hospital=other_hospital,
        content_type=ContentType.objects.get_for_model(other_admission),
        object_id=str(other_admission.id),
        note="Their note",
    )

    assert auth_client.get(f"/api/v1/nursing/notes/{theirs.id}/").status_code == 404
