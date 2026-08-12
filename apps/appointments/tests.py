import datetime
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.appointments.models import Doctor, Slot
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
