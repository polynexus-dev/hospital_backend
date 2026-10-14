"""Phone menu (dynamic IVR), live queue, hold menu, AI call analysis and
missed-opportunity tracking."""
import datetime
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.telephony.models import Call, CallbackTask, IVRMenu, IVRNode, IVRSession


@pytest.fixture
def menu(hospital, department):
    """Welcome menu: 1 = English, 2 = Hindi; each language has 1 = OPD queue,
    2 = timings (info), 3 = callback."""
    menu = IVRMenu.objects.create(hospital=hospital, name="Main line", hold_info_text="OPD runs 9 to 5.", booking_url="https://book.example/x")
    root = IVRNode.objects.create(hospital=hospital, menu=menu, label="Welcome", prompt="Welcome. For English press 1. Hindi ke liye 2 dabaiye.")
    for digit, lang in (("1", "en"), ("2", "hi")):
        branch = IVRNode.objects.create(hospital=hospital, menu=menu, parent=root, digit=digit, label=lang.upper(), action="language", language=lang)
        IVRNode.objects.create(hospital=hospital, menu=menu, parent=branch, digit="1", label="OPD", action="queue", department=department,
                               queue_target="600", queue_agents=1)
        IVRNode.objects.create(hospital=hospital, menu=menu, parent=branch, digit="2", label="Timings", action="info", prompt="We are open 9 to 5.")
        IVRNode.objects.create(hospital=hospital, menu=menu, parent=branch, digit="3", label="Call me back", action="callback")
    return menu


def _step(client, menu, call_id, digits="", caller="9820011111"):
    return client.post(f"/api/v1/ivr/{menu.webhook_token}/step/", {"call_id": call_id, "caller": caller, "digits": digits}, format="json")


# --- Menu ------------------------------------------------------------------

@pytest.mark.django_db
def test_first_step_plays_the_welcome_menu(api_client, menu):
    r = _step(api_client, menu, "c1")
    assert r.status_code == 200
    assert r.data["action"] == "collect"
    assert r.data["say"].startswith("Welcome.")


@pytest.mark.django_db
def test_language_choice_switches_language_and_builds_the_sub_menu(api_client, menu):
    _step(api_client, menu, "c1")
    r = _step(api_client, menu, "c1", "2")
    assert r.data["language"] == "hi"
    assert "OPD के लिए 1 दबाएँ" in r.data["say"]  # no prompt written: built from the options


@pytest.mark.django_db
def test_info_option_reads_the_message_and_repeats_the_menu(api_client, menu):
    _step(api_client, menu, "c1")
    _step(api_client, menu, "c1", "1")
    r = _step(api_client, menu, "c1", "2")
    assert r.data["action"] == "collect"
    assert r.data["say"].startswith("We are open 9 to 5.")
    assert "For OPD, press 1." in r.data["say"]


@pytest.mark.django_db
def test_wrong_keys_repeat_then_hang_up_without_a_fallback(api_client, menu):
    _step(api_client, menu, "c1")
    assert _step(api_client, menu, "c1", "9").data["say"].startswith("Sorry")
    _step(api_client, menu, "c1", "9")
    r = _step(api_client, menu, "c1", "9")
    assert r.data["action"] == "hangup"
    assert IVRSession.objects.get(provider_call_id="c1").state == "ended"


@pytest.mark.django_db
def test_wrong_keys_transfer_to_the_fallback_when_set(api_client, menu):
    menu.fallback_target = "100"
    menu.save()
    _step(api_client, menu, "c1")
    for _ in range(3):
        r = _step(api_client, menu, "c1", "")
    assert r.data == {**r.data, "action": "transfer", "target": "100"}


@pytest.mark.django_db
def test_unknown_or_inactive_menu_is_404(api_client, menu):
    menu.is_active = False
    menu.save()
    assert _step(api_client, menu, "c1").status_code == 404


@pytest.mark.django_db
def test_missing_call_id_is_400(api_client, menu):
    r = api_client.post(f"/api/v1/ivr/{menu.webhook_token}/step/", {"caller": "98"}, format="json")
    assert r.status_code == 400


# --- Queue and hold menu ---------------------------------------------------

def _queue(client, menu, call_id, caller="9820011111"):
    _step(client, menu, call_id, caller=caller)
    _step(client, menu, call_id, "1", caller=caller)
    return _step(client, menu, call_id, "1", caller=caller)


@pytest.mark.django_db
def test_queue_option_queues_the_caller_and_announces_position(api_client, menu):
    r = _queue(api_client, menu, "c1")
    assert r.data["action"] == "queue"
    assert r.data["target"] == "600"
    assert r.data["position"] == 1
    assert "You are next in line." in r.data["say"]
    assert "Press 1" in r.data["say"] and "Press 2" in r.data["say"] and "Press 3" in r.data["say"]


@pytest.mark.django_db
def test_second_caller_is_number_two_with_an_estimated_wait(api_client, menu):
    _queue(api_client, menu, "c1", caller="9820011111")
    r = _queue(api_client, menu, "c2", caller="9820022222")
    assert r.data["position"] == 2
    assert r.data["estimated_wait_seconds"] == 180  # no call history yet: the default handle time
    assert "number 2 in line" in r.data["say"]


@pytest.mark.django_db
def test_position_moves_up_when_the_caller_ahead_is_answered(api_client, menu):
    _queue(api_client, menu, "c1", caller="9820011111")
    _queue(api_client, menu, "c2", caller="9820022222")
    api_client.post(f"/api/v1/ivr/{menu.webhook_token}/event/", {"call_id": "c1", "event": "answered"}, format="json")
    r = api_client.post(f"/api/v1/ivr/{menu.webhook_token}/hold/", {"call_id": "c2"}, format="json")
    assert r.data["action"] == "hold"
    assert r.data["position"] == 1


@pytest.mark.django_db
def test_pressing_1_on_hold_books_one_callback_and_hangs_up(api_client, menu, department):
    _queue(api_client, menu, "c1")
    r = api_client.post(f"/api/v1/ivr/{menu.webhook_token}/hold/", {"call_id": "c1", "digits": "1"}, format="json")
    assert r.data["action"] == "hangup"
    task = CallbackTask.objects.get(phone_number="9820011111")
    assert task.source == "queue"
    assert task.department == department
    assert IVRSession.objects.get(provider_call_id="c1").state == "callback"


@pytest.mark.django_db
def test_callback_request_then_missed_call_record_keeps_one_task(api_client, menu, hospital):
    _queue(api_client, menu, "c1")
    api_client.post(f"/api/v1/ivr/{menu.webhook_token}/hold/", {"call_id": "c1", "digits": "1"}, format="json")
    api_client.post(f"/api/v1/webhooks/telephony/{hospital.id}/", {
        "direction": "inbound", "status": "missed", "from_number": "9820011111", "started_at": timezone.now().isoformat(), "call_id": "c1",
    }, format="json")
    tasks = CallbackTask.objects.filter(phone_number="9820011111")
    assert tasks.count() == 1
    assert tasks.get().call.provider_call_id == "c1"


@pytest.mark.django_db
def test_pressing_2_on_hold_reads_the_hospital_information(api_client, menu):
    _queue(api_client, menu, "c1")
    r = api_client.post(f"/api/v1/ivr/{menu.webhook_token}/hold/", {"call_id": "c1", "digits": "2"}, format="json")
    assert r.data["say"].startswith("OPD runs 9 to 5.")


@pytest.mark.django_db
def test_pressing_3_on_hold_sends_the_booking_link_to_a_new_caller_once(api_client, menu, hospital):
    from django.core.cache import cache

    from apps.enquiries.models import Enquiry

    cache.clear()  # the once-per-call guard is keyed by session id, which the test DB reuses
    _queue(api_client, menu, "c1")
    provider = MagicMock()
    provider.send.return_value = "msg-1"
    with patch("apps.communications.services.get_provider_for_channel", return_value=provider):
        r = api_client.post(f"/api/v1/ivr/{menu.webhook_token}/hold/", {"call_id": "c1", "digits": "3"}, format="json")
        api_client.post(f"/api/v1/ivr/{menu.webhook_token}/hold/", {"call_id": "c1", "digits": "3"}, format="json")
    assert "sent the booking link" in r.data["say"]
    assert provider.send.call_count == 1
    assert "https://book.example/x" in provider.send.call_args.kwargs["body"]
    assert Enquiry.objects.filter(hospital=hospital, source="ivr").count() == 1


@pytest.mark.django_db
def test_hangup_in_queue_marks_abandoned(api_client, menu):
    _queue(api_client, menu, "c1")
    r = api_client.post(f"/api/v1/ivr/{menu.webhook_token}/event/", {"call_id": "c1", "event": "hangup"}, format="json")
    assert r.data["state"] == "abandoned"


@pytest.mark.django_db
def test_finished_call_record_gets_the_menu_path_and_department(api_client, menu, hospital, department):
    _queue(api_client, menu, "c1")
    api_client.post(f"/api/v1/webhooks/telephony/{hospital.id}/", {
        "direction": "inbound", "status": "answered", "from_number": "9820011111", "started_at": timezone.now().isoformat(),
        "call_id": "c1", "duration": 30,
    }, format="json")
    call = Call.objects.get(provider_call_id="c1")
    assert call.ivr_path == "EN > OPD"
    assert call.department == department
    assert IVRSession.objects.get(provider_call_id="c1").state == "answered"


@pytest.mark.django_db
def test_live_queue_lists_waiting_callers_for_staff_only_in_their_hospital(api_client, auth_client, menu, other_hospital):
    _queue(api_client, menu, "c1", caller="9820011111")
    _queue(api_client, menu, "c2", caller="9820022222")
    other = IVRMenu.objects.create(hospital=other_hospital, name="Theirs")
    IVRSession.objects.create(hospital=other_hospital, menu=other, provider_call_id="x", caller="9", state="queued", queued_at=timezone.now())

    r = auth_client.get("/api/v1/call-queue/")
    assert r.status_code == 200
    assert [w["position"] for w in r.data["waiting"]] == [1, 2]
    assert r.data["today"]["queued"] == 2


# --- Menu editor and simulator --------------------------------------------

@pytest.mark.django_db
def test_menu_editor_rejects_a_second_welcome_menu_and_bad_digits(auth_client, menu):
    root = menu.nodes.get(parent__isnull=True)
    r = auth_client.post("/api/v1/ivr-nodes/", {"menu": menu.pk, "label": "Another"}, format="json")
    assert r.status_code == 400 and "parent" in r.data
    r = auth_client.post("/api/v1/ivr-nodes/", {"menu": menu.pk, "parent": root.pk, "digit": "x", "label": "Bad"}, format="json")
    assert r.status_code == 400 and "digit" in r.data
    r = auth_client.post("/api/v1/ivr-nodes/", {"menu": menu.pk, "parent": root.pk, "digit": "5", "label": "Q", "action": "queue"}, format="json")
    assert r.status_code == 400 and "queue_target" in r.data
    r = auth_client.post("/api/v1/ivr-nodes/", {"menu": menu.pk, "parent": root.pk, "digit": "1", "label": "Dup"}, format="json")
    assert r.status_code == 400 and "already used" in str(r.data["digit"])


@pytest.mark.django_db
def test_menu_editor_creates_a_welcome_menu_without_a_key(auth_client, hospital):
    menu = IVRMenu.objects.create(hospital=hospital, name="New")
    r = auth_client.post("/api/v1/ivr-nodes/", {"menu": menu.pk, "label": "Welcome"}, format="json")
    assert r.status_code == 201, r.data
    assert r.data["digit"] == ""


@pytest.mark.django_db
def test_menu_editor_rejects_a_cycle(auth_client, menu):
    root = menu.nodes.get(parent__isnull=True)
    child = root.children.first()
    r = auth_client.patch(f"/api/v1/ivr-nodes/{root.pk}/", {"parent": child.pk, "digit": "7"}, format="json")
    assert r.status_code == 400


@pytest.mark.django_db
def test_menu_detail_includes_webhook_urls_and_rotating_changes_them(auth_client, menu):
    r = auth_client.get(f"/api/v1/ivr-menus/{menu.pk}/")
    assert r.data["webhooks"]["step"].endswith(f"/api/v1/ivr/{menu.webhook_token}/step/")
    old = r.data["webhooks"]["step"]
    rotated = auth_client.post(f"/api/v1/ivr-menus/{menu.pk}/rotate-token/")
    assert rotated.data["webhooks"]["step"] != old


@pytest.mark.django_db
def test_simulator_walks_the_menu_without_touching_the_live_queue(auth_client, menu):
    first = auth_client.post(f"/api/v1/ivr-menus/{menu.pk}/simulate/", {}, format="json")
    call_id = first.data["call_id"]
    auth_client.post(f"/api/v1/ivr-menus/{menu.pk}/simulate/", {"call_id": call_id, "digits": "1"}, format="json")
    queued = auth_client.post(f"/api/v1/ivr-menus/{menu.pk}/simulate/", {"call_id": call_id, "digits": "1"}, format="json")
    assert queued.data["action"] == "queue"
    callback = auth_client.post(f"/api/v1/ivr-menus/{menu.pk}/simulate/", {"call_id": call_id, "digits": "1", "hold": True}, format="json")
    assert callback.data["action"] == "hangup"
    assert not CallbackTask.objects.exists()
    assert auth_client.get("/api/v1/call-queue/").data["waiting"] == []


@pytest.mark.django_db
def test_menus_are_tenant_isolated(auth_client, other_hospital):
    theirs = IVRMenu.objects.create(hospital=other_hospital, name="Theirs")
    assert auth_client.get(f"/api/v1/ivr-menus/{theirs.pk}/").status_code == 404
    assert auth_client.post(f"/api/v1/ivr-menus/{theirs.pk}/simulate/", {}, format="json").status_code == 404


# --- AI call analysis ------------------------------------------------------

def _recorded_call(hospital, **kw):
    return Call.objects.create(hospital=hospital, direction="inbound", status="answered", from_number="9820033333",
                               started_at=timezone.now(), duration_seconds=60, recording_url="https://pbx.example/rec.wav", **kw)


def _fake_http(transcript="Caller wants a knee replacement consult next week.", model_reply=None):
    import json

    model_reply = model_reply or {"summary": "Wants knee replacement consult.", "call_reason": "surgical", "lead_class": "hot", "booking_intent": True}
    audio = MagicMock()
    audio.iter_content.return_value = [b"RIFF...."]
    whisper = MagicMock()
    whisper.json.return_value = {"text": transcript}
    ollama = MagicMock()
    ollama.json.return_value = {"message": {"content": json.dumps(model_reply)}}
    return audio, (lambda url, **kw: whisper if "transcriptions" in url else ollama)


@pytest.mark.django_db
@override_settings(WHISPER_BASE_URL="http://whisper:8000")
def test_analyse_call_fills_summary_labels_and_reason(hospital):
    from apps.telephony.call_ai import analyse_call

    call = _recorded_call(hospital)
    audio, post = _fake_http()
    with patch("apps.telephony.call_ai.requests.get", return_value=audio), patch("apps.telephony.call_ai.requests.post", side_effect=post):
        analyse_call(call)
    call.refresh_from_db()
    assert call.ai_status == "done"
    assert call.transcript.startswith("Caller wants")
    assert call.ai_summary == "Wants knee replacement consult."
    assert (call.call_reason, call.ai_lead_class, call.ai_booking_intent) == ("surgical", "hot", True)


@pytest.mark.django_db
@override_settings(WHISPER_BASE_URL="http://whisper:8000")
def test_analyse_call_keeps_a_staff_set_reason_and_drops_invalid_labels(hospital):
    from apps.telephony.call_ai import analyse_call

    call = _recorded_call(hospital, call_reason="billing")
    audio, post = _fake_http(model_reply={"summary": "x", "call_reason": "surgery!", "lead_class": "lukewarm", "booking_intent": "yes"})
    with patch("apps.telephony.call_ai.requests.get", return_value=audio), patch("apps.telephony.call_ai.requests.post", side_effect=post):
        analyse_call(call)
    call.refresh_from_db()
    assert (call.call_reason, call.ai_lead_class, call.ai_booking_intent) == ("billing", "", None)


@pytest.mark.django_db
@override_settings(WHISPER_BASE_URL="http://whisper:8000")
def test_analyse_call_records_a_failure_instead_of_raising(hospital):
    import requests

    from apps.telephony.call_ai import analyse_call

    call = _recorded_call(hospital)
    with patch("apps.telephony.call_ai.requests.get", side_effect=requests.ConnectionError):
        analyse_call(call)
    call.refresh_from_db()
    assert call.ai_status == "failed"
    assert "download" in call.ai_error


@pytest.mark.django_db
def test_analysis_is_off_without_a_whisper_server(hospital):
    call = _recorded_call(hospital)
    call.refresh_from_db()
    assert call.ai_status == ""


@pytest.mark.django_db(transaction=True)
@override_settings(WHISPER_BASE_URL="http://whisper:8000")
def test_a_recorded_answered_call_is_queued_for_analysis_once(hospital):
    with patch("apps.telephony.tasks.analyse_call.delay") as delay:
        call = _recorded_call(hospital)
        call.notes = "edited"
        call.save()
    delay.assert_called_once_with(call.pk)
    call.refresh_from_db()
    assert call.ai_status == "pending"


@pytest.mark.django_db
def test_analysis_endpoint_and_reanalyse_need_a_recording(auth_client, hospital):
    call = Call.objects.create(hospital=hospital, direction="inbound", status="answered", from_number="9820033334", started_at=timezone.now())
    assert auth_client.get(f"/api/v1/calls/{call.pk}/analysis/").data["transcript"] == ""
    assert auth_client.post(f"/api/v1/calls/{call.pk}/analyse/").status_code == 400


# --- Missed opportunities --------------------------------------------------

@pytest.mark.django_db
def test_unreturned_missed_call_is_flagged_and_a_returned_one_is_not(hospital):
    from apps.telephony.opportunities import flag_missed_opportunities

    two_days_ago = timezone.now() - datetime.timedelta(days=2)
    lost = Call.objects.create(hospital=hospital, direction="inbound", status="missed", from_number="+91 98200 44444", started_at=two_days_ago)
    kept = Call.objects.create(hospital=hospital, direction="inbound", status="missed", from_number="9820055555", started_at=two_days_ago)
    Call.objects.create(hospital=hospital, direction="outbound", status="answered", from_number="101", to_number="09820055555",
                        started_at=two_days_ago + datetime.timedelta(hours=1))

    assert flag_missed_opportunities() == 1
    lost.refresh_from_db()
    kept.refresh_from_db()
    assert lost.missed_opportunity == "unreturned"
    assert kept.missed_opportunity == ""


@pytest.mark.django_db
def test_booking_call_without_a_booking_is_flagged(hospital):
    from apps.enquiries.models import Enquiry
    from apps.telephony.opportunities import flag_missed_opportunities

    three_days_ago = timezone.now() - datetime.timedelta(days=3)
    lost = Call.objects.create(hospital=hospital, direction="inbound", status="answered", from_number="9820066666",
                               started_at=three_days_ago, ai_booking_intent=True, ai_status="done")
    booked = Call.objects.create(hospital=hospital, direction="inbound", status="answered", from_number="9820077777",
                                 started_at=three_days_ago, ai_booking_intent=True, ai_status="done")
    Enquiry.objects.create(hospital=hospital, name="Booked", mobile="9820077777", source="ivr", stage="scheduled")

    flag_missed_opportunities()
    lost.refresh_from_db()
    booked.refresh_from_db()
    assert lost.missed_opportunity == "not_booked"
    assert booked.missed_opportunity == ""


@pytest.mark.django_db
def test_missed_opportunities_report(auth_client, hospital):
    Call.objects.create(hospital=hospital, direction="inbound", status="missed", from_number="9820088888",
                        started_at=timezone.now() - datetime.timedelta(days=2), missed_opportunity="unreturned")
    Call.objects.create(hospital=hospital, direction="inbound", status="answered", from_number="9820099999",
                        started_at=timezone.now() - datetime.timedelta(days=1), ai_status="done", ai_lead_class="hot", ai_booking_intent=True)
    r = auth_client.get("/api/v1/calls/missed-opportunities/")
    assert r.status_code == 200
    assert r.data["summary"]["unreturned"] == 1
    assert r.data["summary"]["lead_classes"]["hot"] == 1
    assert r.data["flagged"][0]["missed_opportunity"] == "unreturned"
