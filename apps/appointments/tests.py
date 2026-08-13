import datetime
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.appointments.models import Appointment, Doctor, Slot, SlotTemplate, Waitlist
from apps.appointments.services import SlotUnavailable, book_appointment
from apps.appointments.tasks import send_appointment_reminders
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


@pytest.mark.django_db
def test_cannot_create_two_slots_at_the_same_doctor_datetime(hospital, doctor, slot):
    with pytest.raises(IntegrityError):
        Slot.objects.create(hospital=hospital, doctor=doctor, date=slot.date, start_time=slot.start_time, end_time=slot.end_time)


@pytest.mark.django_db
def test_double_booking_the_same_slot_is_rejected(hospital, patient, slot):
    another_patient = Patient.objects.create(hospital=hospital, first_name="Rekha", mobile="9988776656")

    book_appointment(patient=patient, slot=slot)

    with pytest.raises(SlotUnavailable):
        book_appointment(patient=another_patient, slot=slot)


@pytest.mark.django_db
def test_booking_stamps_a_unique_registration_token(hospital, patient, slot):
    appointment = book_appointment(patient=patient, slot=slot)
    assert appointment.registration_token


@pytest.mark.django_db
def test_reminder_task_sends_and_stamps_appointments_in_the_24h_window(hospital, doctor, patient):
    target = timezone.localtime() + datetime.timedelta(hours=24, minutes=5)
    start_time = target.time().replace(second=0, microsecond=0)
    end_time = (target + datetime.timedelta(minutes=15)).time().replace(second=0, microsecond=0)
    slot = Slot.objects.create(
        hospital=hospital, doctor=doctor, date=target.date(),
        start_time=start_time, end_time=end_time,
    )
    appointment = book_appointment(patient=patient, slot=slot)

    with patch("apps.communications.services.send_message", return_value=None) as mock_send:
        sent = send_appointment_reminders()

    assert sent == 1
    mock_send.assert_called_once()
    appointment.refresh_from_db()
    assert appointment.reminder_24h_sent_at is not None
    assert appointment.reminder_2h_sent_at is None


# --- Doctor: API CRUD + isolation -----------------------------------------

@pytest.mark.django_db
def test_doctor_api_crud(auth_client, hospital, department):
    create = auth_client.post("/api/v1/doctors/", {"name": "Dr. Iyer"}, format="json")
    assert create.status_code == 201
    created = Doctor.objects.get(pk=create.data["id"])
    assert created.hospital_id == hospital.id

    retrieve = auth_client.get(f"/api/v1/doctors/{created.id}/")
    assert retrieve.status_code == 200 and retrieve.data["name"] == "Dr. Iyer"

    update = auth_client.patch(f"/api/v1/doctors/{created.id}/", {"speciality": "ENT"}, format="json")
    assert update.status_code == 200
    created.refresh_from_db()
    assert created.speciality == "ENT"

    delete = auth_client.delete(f"/api/v1/doctors/{created.id}/")
    assert delete.status_code == 204
    assert not Doctor.objects.filter(pk=created.id).exists()


@pytest.mark.django_db
def test_doctor_isolation(auth_client, other_hospital, other_department):
    theirs = Doctor.objects.create(hospital=other_hospital, department=other_department, name="Not Mine")

    assert Doctor.objects.get(pk=theirs.id)  # sanity: really exists
    assert auth_client.get(f"/api/v1/doctors/{theirs.id}/").status_code == 404
    assert auth_client.patch(f"/api/v1/doctors/{theirs.id}/", {"name": "Hijacked"}, format="json").status_code == 404
    assert auth_client.delete(f"/api/v1/doctors/{theirs.id}/").status_code == 404
    theirs.refresh_from_db()
    assert theirs.name == "Not Mine"


# --- SlotTemplate + generate-slots -----------------------------------------

@pytest.mark.django_db
def test_slot_template_generate_slots_action(auth_client, hospital, doctor):
    create = auth_client.post("/api/v1/slot-templates/", {
        "doctor": doctor.id, "weekday": 0, "start_time": "09:00:00", "end_time": "09:30:00", "slot_duration_minutes": 15,
    }, format="json")
    assert create.status_code == 201
    template_id = create.data["id"]

    response = auth_client.post(f"/api/v1/slot-templates/{template_id}/generate-slots/", {"weeks_ahead": 2}, format="json")

    assert response.status_code == 200
    assert response.data["slots_created"] >= 1
    template = SlotTemplate.objects.get(pk=template_id)
    assert Slot.objects.filter(doctor=template.doctor).exists()


# --- Slot: read-only, no write endpoints -----------------------------------

@pytest.mark.django_db
def test_slot_viewset_has_no_create_endpoint(auth_client, doctor):
    response = auth_client.post("/api/v1/slots/", {
        "doctor": doctor.id, "date": "2027-01-01", "start_time": "09:00:00", "end_time": "09:15:00",
    }, format="json")
    assert response.status_code == 405


@pytest.mark.django_db
def test_slot_available_endpoint_excludes_booked_slots(auth_client, hospital, doctor, patient, slot):
    other_slot = Slot.objects.create(hospital=hospital, doctor=doctor, date=slot.date, start_time=datetime.time(11, 0), end_time=datetime.time(11, 15))
    book_appointment(patient=patient, slot=slot)

    response = auth_client.get("/api/v1/slots/available/")

    ids = {row["id"] for row in response.data}
    assert other_slot.id in ids
    assert slot.id not in ids


@pytest.mark.django_db
def test_slot_isolation(auth_client, other_hospital, other_department):
    their_doctor = Doctor.objects.create(hospital=other_hospital, department=other_department, name="Theirs")
    theirs = Slot.objects.create(hospital=other_hospital, doctor=their_doctor, date=datetime.date.today() + datetime.timedelta(days=1), start_time=datetime.time(9, 0), end_time=datetime.time(9, 15))

    assert auth_client.get(f"/api/v1/slots/{theirs.id}/").status_code == 404
    ids = {row["id"] for row in auth_client.get("/api/v1/slots/").data["results"]}
    assert theirs.id not in ids


# --- Appointment: custom create, actions, PATCH-bypass gotcha, isolation --

@pytest.mark.django_db
def test_booking_via_api_uses_book_appointment_serializer(auth_client, patient, slot):
    response = auth_client.post("/api/v1/appointments/", {"patient": patient.id, "slot": slot.id}, format="json")

    assert response.status_code == 201
    appointment = Appointment.objects.get(pk=response.data["id"])
    assert appointment.slot_id == slot.id
    assert appointment.registration_token


@pytest.mark.django_db
def test_booking_an_already_booked_slot_via_api_returns_409(auth_client, hospital, patient, slot):
    another_patient = Patient.objects.create(hospital=hospital, first_name="Another", mobile="9988776657")
    book_appointment(patient=patient, slot=slot)

    response = auth_client.post("/api/v1/appointments/", {"patient": another_patient.id, "slot": slot.id}, format="json")

    assert response.status_code == 409


@pytest.mark.django_db
def test_appointment_action_lifecycle(auth_client, patient, slot):
    booked = auth_client.post("/api/v1/appointments/", {"patient": patient.id, "slot": slot.id}, format="json").data
    appointment_id = booked["id"]

    checked_in = auth_client.post(f"/api/v1/appointments/{appointment_id}/check-in/")
    assert checked_in.status_code == 200 and checked_in.data["status"] == "checked_in"

    in_consult = auth_client.post(f"/api/v1/appointments/{appointment_id}/start-consult/")
    assert in_consult.data["status"] == "in_consult"

    completed = auth_client.post(f"/api/v1/appointments/{appointment_id}/complete_action/")
    assert completed.data["status"] == "completed"


@pytest.mark.django_db
def test_appointment_patch_onto_an_already_booked_slot_is_caught_by_serializer_uniqueness(auth_client, hospital, doctor, patient, slot):
    """`Appointment.slot` is a OneToOneField, so DRF auto-generates a
    UniqueValidator for it — PATCHing a second appointment onto a slot
    that already has one IS correctly rejected, just at the serializer
    layer (400) rather than services.book_appointment()'s explicit 409.
    Net effect is the same (rejected), so this isn't the double-booking
    hole it might look like at a glance — see the *blocked*-slot test
    below for the gap that uniqueness validation doesn't cover."""
    book_appointment(patient=patient, slot=slot)
    other_patient = Patient.objects.create(hospital=hospital, first_name="Other", mobile="9988776658")
    other_appointment = book_appointment(
        patient=other_patient,
        slot=Slot.objects.create(hospital=hospital, doctor=doctor, date=slot.date, start_time=datetime.time(12, 0), end_time=datetime.time(12, 15)),
    )

    response = auth_client.patch(f"/api/v1/appointments/{other_appointment.id}/", {"slot": slot.id}, format="json")

    assert response.status_code == 400
    other_appointment.refresh_from_db()
    assert other_appointment.slot_id != slot.id  # unchanged
    assert Appointment.objects.filter(slot_id=slot.id).count() == 1


@pytest.mark.django_db
def test_appointment_patch_onto_a_blocked_slot_bypasses_book_appointments_is_blocked_check(auth_client, hospital, doctor, patient, slot):
    """The real gap: `is_blocked` is only checked inside
    services.book_appointment(), which PATCH never calls (ModelViewSet.update
    saves the serializer directly). A blocked slot has no existing
    Appointment, so the OneToOne UniqueValidator above doesn't fire either —
    nothing stops PATCH from moving an appointment onto a slot that was
    deliberately blocked (leave, procedure, etc.). This pins down current,
    real behavior so a future fix is a deliberate decision, not an
    accidental regression."""
    appointment = book_appointment(patient=patient, slot=slot)
    blocked_slot = Slot.objects.create(hospital=hospital, doctor=doctor, date=slot.date, start_time=datetime.time(14, 0), end_time=datetime.time(14, 15), is_blocked=True)

    response = auth_client.patch(f"/api/v1/appointments/{appointment.id}/", {"slot": blocked_slot.id}, format="json")

    assert response.status_code == 200  # not rejected, despite the slot being blocked
    appointment.refresh_from_db()
    assert appointment.slot_id == blocked_slot.id


@pytest.mark.django_db
def test_appointment_isolation(auth_client, other_hospital, other_department):
    their_doctor = Doctor.objects.create(hospital=other_hospital, department=other_department, name="Theirs")
    their_patient = Patient.objects.create(hospital=other_hospital, first_name="Theirs", mobile="9911223344")
    their_slot = Slot.objects.create(hospital=other_hospital, doctor=their_doctor, date=datetime.date.today() + datetime.timedelta(days=1), start_time=datetime.time(9, 0), end_time=datetime.time(9, 15))
    theirs = book_appointment(patient=their_patient, slot=their_slot)

    assert auth_client.get(f"/api/v1/appointments/{theirs.id}/").status_code == 404
    assert auth_client.post(f"/api/v1/appointments/{theirs.id}/check-in/").status_code == 404
    assert auth_client.delete(f"/api/v1/appointments/{theirs.id}/").status_code == 404
    assert Appointment.objects.filter(pk=theirs.id).exists()


# --- Waitlist: confirm/cancel actions --------------------------------------

@pytest.mark.django_db
def test_waitlist_confirm_requires_an_offered_slot(auth_client, hospital, doctor, patient):
    entry = Waitlist.objects.create(hospital=hospital, patient=patient, doctor=doctor)

    response = auth_client.post(f"/api/v1/waitlist/{entry.id}/confirm/")

    assert response.status_code == 400


@pytest.mark.django_db
def test_waitlist_confirm_books_the_offered_slot(auth_client, hospital, doctor, patient, slot):
    entry = Waitlist.objects.create(hospital=hospital, patient=patient, doctor=doctor, offered_slot=slot, status=Waitlist.Status.OFFERED)

    response = auth_client.post(f"/api/v1/waitlist/{entry.id}/confirm/")

    assert response.status_code == 200
    entry.refresh_from_db()
    assert entry.status == Waitlist.Status.BOOKED
    assert entry.resulting_appointment is not None
    assert Appointment.objects.filter(patient=patient, slot=slot).exists()


@pytest.mark.django_db
def test_waitlist_cancel(auth_client, hospital, doctor, patient):
    entry = Waitlist.objects.create(hospital=hospital, patient=patient, doctor=doctor)

    response = auth_client.post(f"/api/v1/waitlist/{entry.id}/cancel/")

    assert response.status_code == 200
    entry.refresh_from_db()
    assert entry.status == Waitlist.Status.CANCELLED


@pytest.mark.django_db
def test_waitlist_delete(auth_client, hospital, doctor, patient):
    entry = Waitlist.objects.create(hospital=hospital, patient=patient, doctor=doctor)

    response = auth_client.delete(f"/api/v1/waitlist/{entry.id}/")

    assert response.status_code == 204
    assert not Waitlist.objects.filter(pk=entry.id).exists()


# --- Paperless registration: public, token-based ---------------------------

@pytest.mark.django_db
def test_paperless_registration_public_get_and_post(api_client, patient, slot):
    appointment = book_appointment(patient=patient, slot=slot)

    get_response = api_client.get(f"/api/v1/registration/{appointment.registration_token}/")
    assert get_response.status_code == 200

    post_response = api_client.post(f"/api/v1/registration/{appointment.registration_token}/")
    assert post_response.status_code == 200
    appointment.refresh_from_db()
    assert appointment.consent_captured is True
    assert appointment.consent_captured_at is not None


@pytest.mark.django_db
def test_paperless_registration_404s_for_an_unknown_token(api_client):
    response = api_client.get("/api/v1/registration/not-a-real-token/")
    assert response.status_code == 404
