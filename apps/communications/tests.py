import datetime

import pytest

from apps.appointments.models import Appointment, Doctor, Slot
from apps.communications.ai_chatbot import process_interactive_chat_action
from apps.communications.models import Channel, ConsentOptOut, Message, Template, Thread
from apps.patients.models import Patient


@pytest.fixture
def doctor(hospital, department):
    return Doctor.objects.create(hospital=hospital, department=department, name="Mehta", speciality="Cardiology")


@pytest.fixture
def slot(hospital, doctor):
    return Slot.objects.create(
        hospital=hospital, doctor=doctor,
        date=datetime.date.today() + datetime.timedelta(days=1),
        start_time=datetime.time(10, 0), end_time=datetime.time(10, 15),
    )


@pytest.mark.django_db
def test_book_opd_lists_real_doctors_not_demo_data(hospital, doctor):
    result = process_interactive_chat_action("book_opd", hospital=hospital)
    option_ids = [o["id"] for o in result["options"]]
    assert f"select_doc_{doctor.id}" in option_ids
    assert not any("doc_kulkarni" in oid or "doc_joshi" in oid for oid in option_ids)


@pytest.mark.django_db
def test_book_opd_never_shows_another_hospitals_doctors(hospital, other_hospital, department):
    Doctor.objects.create(hospital=other_hospital, department=department, name="Not Mine")
    result = process_interactive_chat_action("book_opd", hospital=hospital)
    assert all("Not Mine" not in o["label"] for o in result["options"])


@pytest.mark.django_db
def test_selecting_a_doctor_lists_real_open_slots(hospital, doctor, slot):
    result = process_interactive_chat_action(f"select_doc_{doctor.id}", hospital=hospital)
    assert result["step"] == "select_slot"
    assert any(f"pick_slot_{slot.id}" == o["id"] for o in result["options"])


@pytest.mark.django_db
def test_picking_a_slot_asks_for_name_and_mobile(hospital, doctor, slot):
    result = process_interactive_chat_action(f"pick_slot_{slot.id}", hospital=hospital)
    assert result["step"] == "collect_details"
    assert result["requires_input"] == ["name", "mobile"]
    assert result["pending_slot_id"] == slot.id


@pytest.mark.django_db
def test_submit_booking_creates_a_real_appointment(hospital, doctor, slot):
    result = process_interactive_chat_action(
        "submit_booking",
        payload={"slot_id": slot.id, "name": "Asha Patil", "mobile": "9822011111"},
        hospital=hospital,
    )
    assert result["step"] == "confirmed"

    appointment = Appointment.objects.get(pk=result["confirmed_details"]["appointment_id"])
    assert appointment.slot_id == slot.id
    assert appointment.doctor_id == doctor.id
    assert appointment.source == Appointment.Source.WHATSAPP
    assert appointment.patient.mobile == "9822011111"
    assert appointment.patient.full_name == "Asha Patil"


@pytest.mark.django_db
def test_submit_booking_reuses_existing_patient_by_mobile(hospital, doctor, slot):
    existing = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9822011111")

    result = process_interactive_chat_action(
        "submit_booking",
        payload={"slot_id": slot.id, "name": "Asha Patil", "mobile": "9822011111"},
        hospital=hospital,
    )

    appointment = Appointment.objects.get(pk=result["confirmed_details"]["appointment_id"])
    assert appointment.patient_id == existing.id
    assert Patient.objects.filter(hospital=hospital, mobile="9822011111").count() == 1


@pytest.mark.django_db
def test_submit_booking_rejects_a_slot_already_taken(hospital, doctor, slot):
    other_patient = Patient.objects.create(hospital=hospital, first_name="First", mobile="9822000000")
    from apps.appointments.services import book_appointment
    book_appointment(patient=other_patient, slot=slot)

    result = process_interactive_chat_action(
        "submit_booking",
        payload={"slot_id": slot.id, "name": "Second Patient", "mobile": "9822011111"},
        hospital=hospital,
    )

    assert result["step"] == "select_slot"
    assert Appointment.objects.filter(slot=slot).count() == 1


# --- Template: model + API CRUD, unique_together, render() ----------------

@pytest.fixture
def patient(hospital):
    p = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9822099999")
    yield p
    from contextlib import suppress
    with suppress(Exception):
        p.delete()


@pytest.mark.django_db
def test_template_model_create_and_render(hospital):
    template = Template.objects.create(hospital=hospital, name="Appt confirm", purpose="appointment_confirmation", channel=Channel.WHATSAPP, language=Template.Language.ENGLISH, body="Hi {name}, your visit is confirmed.")
    assert template.render({"name": "Asha"}) == "Hi Asha, your visit is confirmed."


@pytest.mark.django_db
def test_template_render_raises_on_missing_placeholder(hospital):
    template = Template.objects.create(hospital=hospital, name="Appt confirm", purpose="p", channel=Channel.SMS, language=Template.Language.ENGLISH, body="Hi {name}")
    with pytest.raises(KeyError):
        template.render({})


@pytest.mark.django_db
def test_template_api_create_requires_name_purpose_channel_language_body(auth_client, hospital):
    response = auth_client.post("/api/v1/templates/", {
        "name": "Reminder", "purpose": "appointment_reminder", "channel": "whatsapp", "language": "en", "body": "Reminder: your visit is tomorrow.",
    }, format="json")

    assert response.status_code == 201
    created = Template.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id


@pytest.mark.django_db
def test_template_api_create_missing_body_returns_400(auth_client):
    response = auth_client.post("/api/v1/templates/", {"name": "X", "purpose": "p", "channel": "sms", "language": "en"}, format="json")
    assert response.status_code == 400
    assert "body" in response.data


@pytest.mark.django_db
def test_template_unique_together_purpose_channel_language(hospital):
    from django.db import IntegrityError, transaction
    Template.objects.create(hospital=hospital, name="A", purpose="p", channel=Channel.SMS, language=Template.Language.ENGLISH, body="a")
    with pytest.raises(IntegrityError), transaction.atomic():
        Template.objects.create(hospital=hospital, name="B", purpose="p", channel=Channel.SMS, language=Template.Language.ENGLISH, body="b")


@pytest.mark.django_db
def test_template_api_update_and_delete(auth_client, hospital):
    template = Template.objects.create(hospital=hospital, name="A", purpose="p", channel=Channel.SMS, language=Template.Language.ENGLISH, body="a")

    update = auth_client.patch(f"/api/v1/templates/{template.id}/", {"is_active": False}, format="json")
    assert update.status_code == 200
    template.refresh_from_db()
    assert template.is_active is False

    delete = auth_client.delete(f"/api/v1/templates/{template.id}/")
    assert delete.status_code == 204
    assert not Template.objects.filter(pk=template.id).exists()


@pytest.mark.django_db
def test_template_isolation(auth_client, other_hospital):
    theirs = Template.objects.create(hospital=other_hospital, name="Theirs", purpose="p", channel=Channel.SMS, language=Template.Language.ENGLISH, body="x")
    assert auth_client.get(f"/api/v1/templates/{theirs.id}/").status_code == 404
    assert auth_client.delete(f"/api/v1/templates/{theirs.id}/").status_code == 404


# --- ConsentOptOut: model + API CRUD, unique_together -----------------------

@pytest.mark.django_db
def test_consent_opt_out_api_create_stamps_hospital_and_recorded_by(auth_client, hospital, user, patient):
    response = auth_client.post("/api/v1/consent/", {"patient": patient.id, "channel": "whatsapp"}, format="json")

    assert response.status_code == 201
    created = ConsentOptOut.objects.get(pk=response.data["id"])
    assert created.hospital_id == hospital.id
    assert created.recorded_by_id == user.id
    assert created.purpose == ConsentOptOut.Purpose.ALL  # default


@pytest.mark.django_db
def test_consent_opt_out_unique_together_patient_channel_purpose(hospital, patient):
    from django.db import IntegrityError, transaction
    ConsentOptOut.objects.create(hospital=hospital, patient=patient, channel=Channel.WHATSAPP, purpose=ConsentOptOut.Purpose.ALL)
    # atomic() here scopes the broken-transaction state to a savepoint that
    # rolls back on the IntegrityError, instead of poisoning the whole test
    # transaction (which would otherwise take the `patient` fixture's
    # teardown down with it — Postgres refuses any further query on a
    # connection until the failed transaction is rolled back).
    with pytest.raises(IntegrityError), transaction.atomic():
        ConsentOptOut.objects.create(hospital=hospital, patient=patient, channel=Channel.WHATSAPP, purpose=ConsentOptOut.Purpose.ALL)


@pytest.mark.django_db
def test_consent_opt_out_api_update_and_delete(auth_client, hospital, patient):
    consent = ConsentOptOut.objects.create(hospital=hospital, patient=patient, channel=Channel.SMS)

    update = auth_client.patch(f"/api/v1/consent/{consent.id}/", {"is_opted_out": True}, format="json")
    assert update.status_code == 200
    consent.refresh_from_db()
    assert consent.is_opted_out is True

    delete = auth_client.delete(f"/api/v1/consent/{consent.id}/")
    assert delete.status_code == 204
    assert not ConsentOptOut.objects.filter(pk=consent.id).exists()


@pytest.mark.django_db
def test_consent_opt_out_isolation(auth_client, other_hospital, other_department):
    their_patient = Patient.objects.create(hospital=other_hospital, first_name="Theirs", mobile="9822088888")
    theirs = ConsentOptOut.objects.create(hospital=other_hospital, patient=their_patient, channel=Channel.SMS)
    assert auth_client.get(f"/api/v1/consent/{theirs.id}/").status_code == 404
    assert auth_client.delete(f"/api/v1/consent/{theirs.id}/").status_code == 404


# --- Message: read-only viewset + send action ------------------------------

@pytest.mark.django_db
def test_message_viewset_has_no_create_endpoint(auth_client, patient):
    response = auth_client.post("/api/v1/messages/", {"patient": patient.id, "channel": "sms", "direction": "outbound"}, format="json")
    assert response.status_code == 405


@pytest.mark.django_db
def test_send_message_without_a_template_returns_422(auth_client, patient):
    response = auth_client.post("/api/v1/messages/send/", {"patient": patient.id, "channel": "sms", "purpose": "no_template_exists_for_this"}, format="json")
    assert response.status_code == 422


@pytest.mark.django_db
def test_send_message_with_an_active_template_succeeds(auth_client, hospital, patient):
    Template.objects.create(hospital=hospital, name="Greeting", purpose="greeting", channel=Channel.SMS, language=patient.preferred_language, body="Hello {name}")

    response = auth_client.post("/api/v1/messages/send/", {"patient": patient.id, "channel": "sms", "purpose": "greeting", "context": {"name": patient.first_name}}, format="json")

    assert response.status_code == 201
    message = Message.objects.get(pk=response.data["id"])
    assert message.direction == Message.Direction.OUTBOUND
    assert message.status in (Message.Status.SENT, Message.Status.FAILED)


@pytest.mark.django_db
def test_send_message_to_an_opted_out_patient_with_no_fallback_returns_422(auth_client, hospital, patient):
    ConsentOptOut.objects.create(hospital=hospital, patient=patient, channel=Channel.SMS, is_opted_out=True, purpose=ConsentOptOut.Purpose.ALL)
    Template.objects.create(hospital=hospital, name="Greeting", purpose="greeting", channel=Channel.SMS, language=patient.preferred_language, body="Hello")

    response = auth_client.post("/api/v1/messages/send/", {"patient": patient.id, "channel": "sms", "purpose": "greeting"}, format="json")

    assert response.status_code == 422


@pytest.mark.django_db
def test_message_list_isolation(auth_client, hospital, other_hospital):
    their_patient = Patient.objects.create(hospital=other_hospital, first_name="Theirs", mobile="9822077777")
    Message.objects.create(hospital=other_hospital, patient=their_patient, channel=Channel.SMS, direction=Message.Direction.OUTBOUND, body="secret")

    response = auth_client.get("/api/v1/messages/")

    bodies = {row["body"] for row in response.data["results"]}
    assert "secret" not in bodies


# --- Thread: read-only viewset + claim/mark_read actions --------------------

@pytest.mark.django_db
def test_thread_viewset_has_no_create_endpoint(auth_client, hospital, patient):
    response = auth_client.post("/api/v1/threads/", {"patient": patient.id, "channel": "whatsapp"}, format="json")
    assert response.status_code == 405


@pytest.mark.django_db
def test_thread_claim_assigns_the_requesting_user_as_owner(auth_client, hospital, user, patient):
    thread = Thread.objects.create(hospital=hospital, patient=patient, channel=Channel.WHATSAPP)

    response = auth_client.post(f"/api/v1/threads/{thread.id}/claim/")

    assert response.status_code == 200
    thread.refresh_from_db()
    assert thread.owner_id == user.id


@pytest.mark.django_db
def test_thread_mark_read_clears_unread_inbound_messages(auth_client, hospital, patient):
    thread = Thread.objects.create(hospital=hospital, patient=patient, channel=Channel.WHATSAPP)
    Message.objects.create(hospital=hospital, patient=patient, channel=Channel.WHATSAPP, direction=Message.Direction.INBOUND, body="hi", is_read=False)

    response = auth_client.post(f"/api/v1/threads/{thread.id}/mark_read/")

    assert response.status_code == 200
    assert not Message.objects.filter(patient=patient, direction=Message.Direction.INBOUND, is_read=False).exists()


@pytest.mark.django_db
def test_thread_isolation(auth_client, other_hospital):
    their_patient = Patient.objects.create(hospital=other_hospital, first_name="Theirs", mobile="9822066666")
    theirs = Thread.objects.create(hospital=other_hospital, patient=their_patient, channel=Channel.WHATSAPP)
    assert auth_client.get(f"/api/v1/threads/{theirs.id}/").status_code == 404
    assert auth_client.post(f"/api/v1/threads/{theirs.id}/claim/").status_code == 404


# --- InboundWebhookView: public, AllowAny -----------------------------------

@pytest.mark.django_db
def test_inbound_webhook_logs_a_message_and_triggers_ai_auto_reply(api_client, hospital, patient):
    response = api_client.post(f"/api/v1/webhooks/inbound/{hospital.id}/whatsapp/", {
        "from": patient.mobile, "body": "Hi, what are your OPD timings?",
    }, format="json")

    assert response.status_code == 201
    assert "inbound" in response.data and "ai_auto_reply" in response.data
    assert Message.objects.filter(hospital=hospital, patient=patient, direction=Message.Direction.INBOUND).exists()


@pytest.mark.django_db
def test_inbound_webhook_with_no_matching_patient_is_dropped_not_errored(api_client, hospital):
    response = api_client.post(f"/api/v1/webhooks/inbound/{hospital.id}/whatsapp/", {"from": "0000000000", "body": "hi"}, format="json")
    assert response.status_code == 202
    assert not Message.objects.filter(hospital=hospital).exists()
