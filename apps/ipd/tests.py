import pytest

from apps.accounts.models import Role, User, assign_role
from apps.appointments.models import Doctor
from apps.facilities.models import Bed, Room, Ward
from apps.ipd.models import Admission, BedAllocation, DischargeSummary, DoctorProgressNote
from apps.ipd.services import (
    BedUnavailable,
    DischargeSummaryRequired,
    admit_patient,
    approve_ward_transfer,
    discharge_patient,
    request_ward_transfer,
)
from apps.patients.models import Patient


@pytest.fixture
def doctor(hospital, department):
    return Doctor.objects.create(hospital=hospital, department=department, name="Mehta")


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Sunita", mobile="9988776655")


@pytest.fixture
def ward(hospital):
    return Ward.objects.create(hospital=hospital, name="General Ward A")


@pytest.fixture
def room(hospital, ward):
    return Room.objects.create(hospital=hospital, ward=ward, room_number="101")


@pytest.fixture
def bed(hospital, room):
    return Bed.objects.create(hospital=hospital, room=room, bed_number="A")


@pytest.fixture
def other_bed(hospital, room):
    return Bed.objects.create(hospital=hospital, room=room, bed_number="B")


@pytest.fixture
def admission(hospital, patient, doctor, bed):
    return admit_patient(hospital=hospital, patient=patient, admitting_doctor=doctor, bed=bed)


@pytest.fixture
def doctor_user(hospital, department, doctor):
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


# --- admit_patient: bed locking, BedAllocation, occupancy sync ------------

@pytest.mark.django_db
def test_admit_patient_creates_admission_and_occupies_the_bed(hospital, patient, doctor, bed):
    admission = admit_patient(hospital=hospital, patient=patient, admitting_doctor=doctor, bed=bed)

    assert admission.status == Admission.Status.ADMITTED
    bed.refresh_from_db()
    assert bed.status == Bed.Status.OCCUPIED
    assert bed.current_admission_id == admission.id
    assert BedAllocation.objects.filter(admission=admission, bed=bed, released_at__isnull=True).exists()


@pytest.mark.django_db
def test_admit_patient_rejects_an_already_occupied_bed(hospital, patient, doctor, bed):
    admit_patient(hospital=hospital, patient=patient, admitting_doctor=doctor, bed=bed)
    second_patient = Patient.objects.create(hospital=hospital, first_name="Second", mobile="9988776656")

    with pytest.raises(BedUnavailable):
        admit_patient(hospital=hospital, patient=second_patient, admitting_doctor=doctor, bed=bed)


# --- Ward transfer: request + approve, bed sync ---------------------------

@pytest.mark.django_db
def test_ward_transfer_approval_moves_the_admission_to_the_new_bed(hospital, admission, other_bed):
    transfer = request_ward_transfer(admission=admission, to_bed=other_bed, reason="Needs closer monitoring", requested_by=None)
    assert transfer.approved_by is None

    approved = approve_ward_transfer(transfer, approved_by=None)

    admission.refresh_from_db()
    assert admission.bed_id == other_bed.id
    assert approved.transferred_at is not None

    old_bed = Bed.objects.get(pk=transfer.from_bed_id)
    assert old_bed.status == Bed.Status.AVAILABLE
    assert old_bed.current_admission_id is None

    other_bed.refresh_from_db()
    assert other_bed.status == Bed.Status.OCCUPIED
    assert other_bed.current_admission_id == admission.id

    assert BedAllocation.objects.filter(admission=admission, bed=transfer.from_bed, released_at__isnull=False).exists()
    assert BedAllocation.objects.filter(admission=admission, bed=other_bed, released_at__isnull=True).exists()


@pytest.mark.django_db
def test_ward_transfer_approval_rejects_an_occupied_destination_bed(hospital, patient, doctor, admission, other_bed):
    other_patient = Patient.objects.create(hospital=hospital, first_name="Other", mobile="9988776657")
    admit_patient(hospital=hospital, patient=other_patient, admitting_doctor=doctor, bed=other_bed)

    transfer = request_ward_transfer(admission=admission, to_bed=other_bed, reason="x", requested_by=None)
    with pytest.raises(BedUnavailable):
        approve_ward_transfer(transfer, approved_by=None)


# --- Discharge: requires a DischargeSummary, releases the bed, fires the event ---

@pytest.mark.django_db
def test_discharge_requires_a_discharge_summary_to_exist_first(admission):
    with pytest.raises(DischargeSummaryRequired):
        discharge_patient(admission)


@pytest.mark.django_db
def test_discharge_releases_the_bed_and_updates_admission_status(hospital, admission):
    DischargeSummary.objects.create(hospital=hospital, admission=admission)

    discharged = discharge_patient(admission)

    assert discharged.status == Admission.Status.DISCHARGED
    assert discharged.discharged_at is not None
    bed = Bed.objects.get(pk=discharged.bed_id)
    assert bed.status == Bed.Status.AVAILABLE
    assert bed.current_admission_id is None
    assert BedAllocation.objects.filter(admission=admission, released_at__isnull=False).exists()


@pytest.mark.django_db
def test_discharge_fires_the_patient_discharged_signal(hospital, admission):
    from apps.ipd.signals import patient_discharged

    DischargeSummary.objects.create(hospital=hospital, admission=admission)
    received = []

    def _handler(sender, admission, **kw):
        received.append(admission.id)

    # weak=False — .connect() defaults to a weak reference, and a receiver
    # with no other live reference (like a bare lambda would be here) gets
    # garbage-collected before the signal ever fires, making the assertion
    # below silently see nothing sent rather than failing loudly on connect.
    patient_discharged.connect(_handler, weak=False)

    discharge_patient(admission)

    assert received == [admission.id]


# --- Finalize-locking: DoctorProgressNote, DischargeSummary --------------

@pytest.mark.django_db
def test_doctor_progress_note_finalize_locks_it(hospital, admission, doctor):
    note = DoctorProgressNote.objects.create(hospital=hospital, admission=admission, doctor=doctor, note="Stable overnight.")
    note.finalize(None)

    note.note = "Tampered"
    with pytest.raises(ValueError):
        note.save()


@pytest.mark.django_db
def test_discharge_summary_finalize_locks_it(hospital, admission):
    summary = DischargeSummary.objects.create(hospital=hospital, admission=admission, final_diagnosis="Viral fever")
    summary.finalize(None)

    summary.final_diagnosis = "Tampered"
    with pytest.raises(ValueError):
        summary.save()


# --- API: admission create/discharge, RBAC, isolation ---------------------

@pytest.mark.django_db
def test_admission_api_create(doctor_client, hospital, patient, doctor, bed):
    response = doctor_client.post("/api/v1/ipd/admissions/", {
        "patient": patient.id, "admitting_doctor": doctor.id, "bed": bed.id,
    }, format="json")
    assert response.status_code == 201
    created = Admission.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
    bed.refresh_from_db()
    assert bed.status == Bed.Status.OCCUPIED


@pytest.mark.django_db
def test_admission_api_create_conflicts_on_an_occupied_bed(doctor_client, hospital, patient, doctor, bed):
    admit_patient(hospital=hospital, patient=patient, admitting_doctor=doctor, bed=bed)
    other_patient = Patient.objects.create(hospital=hospital, first_name="Other", mobile="9988776658")

    response = doctor_client.post("/api/v1/ipd/admissions/", {
        "patient": other_patient.id, "admitting_doctor": doctor.id, "bed": bed.id,
    }, format="json")
    assert response.status_code == 409


@pytest.mark.django_db
def test_admission_discharge_action_requires_a_discharge_summary_first(doctor_client, hospital, admission):
    response = doctor_client.post(f"/api/v1/ipd/admissions/{admission.id}/discharge/")
    assert response.status_code == 400

    doctor_client.post("/api/v1/ipd/discharge-summaries/", {"admission": admission.id}, format="json")

    response = doctor_client.post(f"/api/v1/ipd/admissions/{admission.id}/discharge/")
    assert response.status_code == 200
    assert response.data["status"] == "discharged"


@pytest.mark.django_db
def test_a_doctor_with_assigned_only_scope_sees_only_their_own_admissions(hospital, department, doctor, doctor_user, admission, patient, other_bed):
    other_doctor = Doctor.objects.create(hospital=hospital, department=department, name="Iyer")
    other_admission = admit_patient(hospital=hospital, patient=patient, admitting_doctor=other_doctor, bed=other_bed)

    from rest_framework.test import APIClient
    client = APIClient()
    client.force_authenticate(user=doctor_user)

    response = client.get("/api/v1/ipd/admissions/")
    ids = [a["id"] for a in response.data["results"]]
    assert admission.id in ids
    assert other_admission.id not in ids


@pytest.mark.django_db
def test_receptionist_role_is_blocked_from_every_ipd_endpoint(receptionist_client, admission):
    assert receptionist_client.get("/api/v1/ipd/admissions/").status_code == 403
    assert receptionist_client.get("/api/v1/ipd/discharge-summaries/").status_code == 403
    assert receptionist_client.get("/api/v1/ipd/progress-notes/").status_code == 403


@pytest.mark.django_db
def test_nurse_template_has_view_only_access_to_ipd(hospital, department):
    role = Role.objects.create(hospital=hospital, department=department, name="Nurse", template=Role.Template.NURSE)
    nurse = User.objects.create_user(email="nurse@test-hospital.example", password="testpass123", hospital=hospital, department=department)
    assign_role(nurse, role)

    assert nurse.has_perm("ipd.view_admission")
    assert not nurse.has_perm("ipd.add_admission")
    assert nurse.has_perm("patients.access_clinical_detail")


@pytest.mark.django_db
def test_admission_isolation(auth_client, other_hospital, other_department):
    other_doctor = Doctor.objects.create(hospital=other_hospital, department=other_department, name="Theirs")
    other_patient = Patient.objects.create(hospital=other_hospital, first_name="Theirs", mobile="9000000098")
    other_ward = Ward.objects.create(hospital=other_hospital, name="Ward")
    other_room = Room.objects.create(hospital=other_hospital, ward=other_ward, room_number="1")
    other_bed_obj = Bed.objects.create(hospital=other_hospital, room=other_room, bed_number="A")
    theirs = admit_patient(hospital=other_hospital, patient=other_patient, admitting_doctor=other_doctor, bed=other_bed_obj)

    assert auth_client.get(f"/api/v1/ipd/admissions/{theirs.id}/").status_code == 404
