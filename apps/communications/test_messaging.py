"""Real campaigns, MSG91 SMS, polyMX email, delivery reports, unsubscribes,
STOP replies and the "lead went quiet" follow-up."""
import datetime
from unittest.mock import MagicMock, patch

import pytest
from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.communications.models import BroadcastCampaign, ConsentOptOut, ContactSuppression, Message, Template
from apps.enquiries.models import Enquiry
from apps.patients.models import Patient


def _sms_template(hospital, **kw):
    return Template.objects.create(hospital=hospital, name="Offer", purpose="offer", channel="sms", language="en",
                                   body="Hi {patient_name}, offer at {hospital_name}", **kw)


# --- MSG91 -------------------------------------------------------------------

@override_settings(MSG91_AUTH_KEY="key-1")
@pytest.mark.django_db
def test_msg91_sends_the_dlt_template_with_its_variables(hospital):
    from apps.communications.adapters import MSG91SMSProvider

    template = _sms_template(hospital, provider_template_id="tpl-9", provider_variables="patient_name, hospital_name")
    response = MagicMock(status_code=200)
    response.json.return_value = {"type": "success", "message": "req-123"}
    with patch("requests.post", return_value=response) as post:
        rid = MSG91SMSProvider().send(to="+91 98220 11111", body="x", template=template, context={"patient_name": "Asha", "hospital_name": "City"})
    assert rid == "req-123"
    sent = post.call_args.kwargs
    assert sent["headers"]["authkey"] == "key-1"
    assert sent["json"] == {"template_id": "tpl-9", "short_url": "0",
                            "recipients": [{"mobiles": "919822011111", "patient_name": "Asha", "hospital_name": "City"}]}


@override_settings(MSG91_AUTH_KEY="key-1")
@pytest.mark.django_db
def test_msg91_refuses_free_text_and_reports_provider_errors(hospital):
    from apps.communications.adapters import MSG91SMSProvider, SendRefused

    with pytest.raises(SendRefused):
        MSG91SMSProvider().send(to="9822011111", body="free text")
    template = _sms_template(hospital, provider_template_id="tpl-9")
    response = MagicMock(status_code=200)
    response.json.return_value = {"type": "error", "message": "Template not approved"}
    with patch("requests.post", return_value=response), pytest.raises(RuntimeError, match="Template not approved"):
        MSG91SMSProvider().send(to="9822011111", body="x", template=template, context={})


# --- polyMX email (SMTP) -----------------------------------------------------

@override_settings(EMAIL_PROVIDER="polymx", MESSAGING_FROM_EMAIL="Polynexus <care@mail.example.in>",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
@pytest.mark.django_db
def test_marketing_email_goes_out_from_the_hospital_with_an_unsubscribe_link(hospital):
    from apps.communications.services import deliver

    enquiry = Enquiry.objects.create(hospital=hospital, name="Ravi", mobile="9822022222", email="ravi@example.com", source="website")
    msg = deliver(hospital=hospital, channel="email", to="ravi@example.com", body="Hello https://x.example", subject="Hi",
                  context={"hospital_name": "City Hospital"}, enquiry=enquiry, marketing=True)
    assert msg.status == "sent"
    email = mail.outbox[0]
    assert email.from_email == "City Hospital <care@mail.example.in>"
    assert "/api/v1/unsubscribe/" in email.body
    assert email.extra_headers["List-Unsubscribe"].startswith("<http")
    assert 'href="https://x.example"' in email.alternatives[0][0]


@pytest.mark.django_db
def test_a_template_with_an_unfilled_placeholder_is_logged_as_failed_not_sent(hospital):
    from apps.communications.services import send_message

    patient = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9822033334")
    Template.objects.create(hospital=hospital, name="Typo", purpose="typo", channel="whatsapp", language="en", body="Hi {patient_nmae}")
    provider = MagicMock()
    with patch("apps.communications.services.get_provider_for_channel", return_value=provider):
        msg = send_message(patient=patient, channel="whatsapp", purpose="typo", context={"patient_name": "Asha"})
    assert provider.send.call_count == 0
    assert msg.status == "failed"
    assert "patient_nmae" in msg.error


@pytest.mark.django_db
def test_a_provider_failure_is_recorded_not_raised(hospital):
    from apps.communications.services import deliver

    patient = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9822033333")
    broken = MagicMock()
    broken.send.side_effect = ConnectionError("down")
    with patch("apps.communications.services.get_provider_for_channel", return_value=broken):
        msg = deliver(hospital=hospital, channel="whatsapp", to="9822033333", body="x", patient=patient)
    assert msg.status == "failed"
    assert "down" in msg.error


# --- Campaigns ---------------------------------------------------------------

@pytest.fixture
def people(hospital):
    a = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9822044441")
    b = Patient.objects.create(hospital=hospital, first_name="Bina", mobile="9822044442")
    c = Patient.objects.create(hospital=hospital, first_name="Chetan", mobile="9822044443")
    ConsentOptOut.objects.create(hospital=hospital, patient=b, channel="whatsapp", purpose="marketing", is_opted_out=True)
    Patient.objects.create(hospital=hospital, first_name="NoPhone", mobile="")
    return a, b, c


@pytest.mark.django_db
def test_campaign_sends_real_messages_and_counts_only_what_happened(hospital, user, people):
    from apps.communications.campaigns import send_campaign

    campaign = BroadcastCampaign.objects.create(hospital=hospital, title="Camp", channel="whatsapp", target_audience="all_patients",
                                                custom_message="Hi {{patient_name}}, free check-up", status="sending", created_by=user)
    provider = MagicMock()
    provider.send.side_effect = ["wa-1", RuntimeError("bad number")]
    with patch("apps.communications.services.get_provider_for_channel", return_value=provider):
        send_campaign(campaign)
    campaign.refresh_from_db()
    assert (campaign.total_recipients, campaign.sent_count, campaign.failed_count, campaign.skipped_count) == (4, 1, 1, 2)
    assert (campaign.delivered_count, campaign.read_count) == (0, 0)
    assert campaign.status == "completed"
    bodies = {m.body for m in Message.objects.filter(campaign=campaign)}
    assert bodies == {"Hi Asha, free check-up", "Hi Chetan, free check-up"}


@pytest.mark.django_db
def test_rerunning_a_campaign_does_not_message_anyone_twice(hospital, people):
    from apps.communications.campaigns import send_campaign

    campaign = BroadcastCampaign.objects.create(hospital=hospital, title="Camp", channel="whatsapp", target_audience="all_patients",
                                                custom_message="Hi", status="sending")
    provider = MagicMock()
    provider.send.return_value = "wa"
    with patch("apps.communications.services.get_provider_for_channel", return_value=provider):
        send_campaign(campaign)
        send_campaign(campaign)
    assert provider.send.call_count == 2  # Asha and Chetan, once each


@pytest.mark.django_db
def test_retrying_a_campaign_resends_only_the_failures(hospital, people):
    from apps.communications.campaigns import send_campaign

    campaign = BroadcastCampaign.objects.create(hospital=hospital, title="Camp", channel="whatsapp", target_audience="all_patients",
                                                custom_message="Hi", status="sending")
    provider = MagicMock()
    provider.send.side_effect = ["wa-1", RuntimeError("down")]
    with patch("apps.communications.services.get_provider_for_channel", return_value=provider):
        send_campaign(campaign)
        provider.send.side_effect = None
        provider.send.return_value = "wa-2"
        send_campaign(campaign)
    campaign.refresh_from_db()
    assert provider.send.call_count == 3
    assert (campaign.sent_count, campaign.failed_count, campaign.skipped_count) == (2, 0, 2)


@pytest.mark.django_db
def test_lead_campaign_reaches_open_leads_and_skips_suppressed_numbers(hospital):
    from apps.communications.campaigns import send_campaign
    from apps.communications.services import contact_hash

    Enquiry.objects.create(hospital=hospital, name="Open", mobile="9822055551", source="website", stage="new")
    Enquiry.objects.create(hospital=hospital, name="Lost", mobile="9822055552", source="website", stage="lost")
    Enquiry.objects.create(hospital=hospital, name="Stopped", mobile="9822055553", source="website", stage="contacted")
    ContactSuppression.objects.create(hospital=hospital, channel="whatsapp", contact_hash=contact_hash("whatsapp", "9822055553"))
    campaign = BroadcastCampaign.objects.create(hospital=hospital, title="Leads", channel="whatsapp", target_audience="unconverted_leads",
                                                custom_message="Hello {patient_name}", status="sending")
    provider = MagicMock()
    provider.send.return_value = "wa"
    with patch("apps.communications.services.get_provider_for_channel", return_value=provider):
        send_campaign(campaign)
    campaign.refresh_from_db()
    assert (campaign.total_recipients, campaign.sent_count, campaign.skipped_count) == (2, 1, 1)
    assert provider.send.call_args.kwargs["body"] == "Hello Open"


@pytest.mark.django_db
def test_dispatch_rejects_an_sms_campaign_without_a_dlt_template(auth_client, hospital):
    campaign = BroadcastCampaign.objects.create(hospital=hospital, title="SMS", channel="sms", target_audience="all_patients", custom_message="Hi")
    r = auth_client.post(f"/api/v1/broadcasts/{campaign.pk}/dispatch/")
    assert r.status_code == 400
    assert "DLT" in r.data["detail"]


@pytest.mark.django_db
def test_dispatch_schedules_a_future_campaign_and_sends_a_due_one(auth_client, hospital, django_capture_on_commit_callbacks):
    later = BroadcastCampaign.objects.create(hospital=hospital, title="Later", channel="whatsapp", target_audience="all_patients",
                                             custom_message="Hi", scheduled_for=timezone.now() + datetime.timedelta(days=1))
    assert auth_client.post(f"/api/v1/broadcasts/{later.pk}/dispatch/").data["campaign"]["status"] == "scheduled"

    now = BroadcastCampaign.objects.create(hospital=hospital, title="Now", channel="whatsapp", target_audience="all_patients", custom_message="Hi")
    with patch("apps.communications.tasks.send_campaign_task.delay") as delay, django_capture_on_commit_callbacks(execute=True):
        r = auth_client.post(f"/api/v1/broadcasts/{now.pk}/dispatch/")
    assert r.data["campaign"]["status"] == "sending"
    delay.assert_called_once_with(now.pk)
    assert auth_client.post(f"/api/v1/broadcasts/{now.pk}/dispatch/").status_code == 400  # already sending


@pytest.mark.django_db
def test_audience_counts_are_real(auth_client, hospital, people):
    Patient.objects.create(hospital=hospital, first_name="Old", mobile="9822066661", date_of_birth=datetime.date(1950, 5, 1))
    r = auth_client.get("/api/v1/broadcasts/audience-count/")
    assert r.data["all_patients"] == 5
    assert r.data["senior_citizens"] == 1
    assert r.data["chronic_care"] == 0


@pytest.mark.django_db
def test_sent_campaign_cannot_be_edited(auth_client, hospital):
    campaign = BroadcastCampaign.objects.create(hospital=hospital, title="Done", channel="whatsapp", target_audience="all_patients",
                                                custom_message="Hi", status="completed")
    assert auth_client.patch(f"/api/v1/broadcasts/{campaign.pk}/", {"title": "x"}, format="json").status_code == 400


# --- Delivery reports ----------------------------------------------------------

@override_settings(MSG91_WEBHOOK_TOKEN="hook-1")
@pytest.mark.django_db
def test_msg91_reports_update_messages_and_campaign_counts(api_client, hospital):
    patient = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9822077771")
    campaign = BroadcastCampaign.objects.create(hospital=hospital, title="C", channel="sms", target_audience="all_patients", custom_message="x")
    for rid in ("r1", "r2"):
        Message.objects.create(hospital=hospital, patient=patient, channel="sms", direction="outbound", body="x", status="sent",
                               provider_message_id=rid, campaign=campaign)
    api_client.post("/api/v1/webhooks/msg91/hook-1/", {"requestId": "r1", "status": "Delivered", "telNum": "919822077771"}, format="json")
    api_client.post("/api/v1/webhooks/msg91/hook-1/", [{"requestId": "r2", "report": [{"status": "2", "desc": "FAILED"}]}], format="json")
    campaign.refresh_from_db()
    assert (campaign.sent_count, campaign.delivered_count, campaign.failed_count) == (1, 1, 1)
    assert api_client.post("/api/v1/webhooks/msg91/wrong/", {}, format="json").status_code == 403


@pytest.mark.django_db
def test_delivery_status_never_moves_backwards(hospital):
    from apps.communications.delivery import apply_delivery_status

    patient = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9822077772")
    msg = Message.objects.create(hospital=hospital, patient=patient, channel="whatsapp", direction="outbound", body="x", status="read",
                                 provider_message_id="w1")
    assert apply_delivery_status("w1", "delivered") == 0
    assert apply_delivery_status("w1", "failed") == 0
    msg.refresh_from_db()
    assert msg.status == "read"


# --- Unsubscribe and STOP ------------------------------------------------------

@pytest.mark.django_db
def test_unsubscribe_link_asks_on_get_and_suppresses_on_post(api_client, hospital):
    from apps.communications.services import is_suppressed, unsubscribe_url

    patient = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9822088881", email="asha@example.com")
    path = unsubscribe_url(hospital.pk, "email", "asha@example.com").split("://", 1)[1].split("/", 1)[1]
    assert b"Unsubscribe me" in api_client.get(f"/{path}").content
    assert not is_suppressed(hospital.pk, "email", "asha@example.com")
    assert api_client.post(f"/{path}").status_code == 200
    assert is_suppressed(hospital.pk, "email", "ASHA@example.com")
    assert ConsentOptOut.objects.get(patient=patient, channel="email").purpose == "marketing"
    assert api_client.get("/api/v1/unsubscribe/tampered/").status_code == 400


@pytest.mark.django_db
def test_stop_reply_opts_the_number_out_of_marketing(api_client, hospital):
    from apps.communications.services import is_suppressed

    r = api_client.post(f"/api/v1/webhooks/inbound/{hospital.pk}/whatsapp/", {"from": "9822099991", "body": "stop"}, format="json")
    assert r.status_code == 202
    assert is_suppressed(hospital.pk, "whatsapp", "9822099991")


# --- Lead went quiet -----------------------------------------------------------

def _age(enquiry, days):
    Enquiry.objects.filter(pk=enquiry.pk).update(created_at=timezone.now() - datetime.timedelta(days=days))


@pytest.mark.django_db
def test_quiet_lead_starts_the_workflow_once_per_quiet_spell(hospital):
    from apps.automation.models import Workflow
    from apps.enquiries.quiet import sweep_quiet_enquiries

    Workflow.objects.create(hospital=hospital, name="Quiet", trigger_type="enquiry_went_quiet", is_active=True)
    quiet = Enquiry.objects.create(hospital=hospital, name="Quiet", mobile="9822010101", source="website")
    fresh = Enquiry.objects.create(hospital=hospital, name="Fresh", mobile="9822010102", source="website")
    _age(quiet, 3)
    with patch("apps.automation.engine.fire") as fire:
        assert sweep_quiet_enquiries() == 1
        assert sweep_quiet_enquiries() == 0  # same spell
    assert fire.call_args.args[1]["enquiry_id"] == quiet.pk
    fresh.refresh_from_db()
    assert fresh.went_quiet_at is None


@pytest.mark.django_db
def test_our_own_follow_up_does_not_end_the_quiet_spell_but_a_reply_does(hospital):
    from apps.enquiries.quiet import quiet_days

    enquiry = Enquiry.objects.create(hospital=hospital, name="Q", mobile="9822010103", source="website")
    _age(enquiry, 6)
    Message.objects.create(hospital=hospital, enquiry=enquiry, channel="whatsapp", direction="outbound", body="nudge", status="sent")
    assert quiet_days(enquiry) >= 5.9
    Message.objects.create(hospital=hospital, enquiry=enquiry, channel="whatsapp", direction="inbound", body="yes please", status="received")
    assert quiet_days(enquiry) < 0.1


@pytest.mark.django_db
def test_ready_made_quiet_sequence_installs_and_its_email_step_sends(hospital, user):
    from apps.automation.engine import execute_workflow
    from apps.automation.workflow_templates import install

    wf = install("lead_went_quiet_sequence", hospital, user)
    wf.is_active = True
    wf.save()
    enquiry = Enquiry.objects.create(hospital=hospital, name="Ravi", mobile="9822010104", email="ravi@example.com", source="website")
    provider = MagicMock()
    provider.send.return_value = "id-1"
    with patch("apps.communications.services.get_provider_for_channel", return_value=provider):
        run = execute_workflow("enquiry_went_quiet", {"enquiry_id": enquiry.pk}, hospital.pk)[0]
        assert run.status == "waiting"  # WhatsApp sent, now waiting 3 days
        _age(enquiry, 6)
        from apps.automation.engine import resume_due

        type(run).objects.filter(pk=run.pk).update(resume_at=timezone.now())
        resume_due()
    assert list(Message.objects.filter(enquiry=enquiry).order_by("created_at").values_list("channel", flat=True)) == ["whatsapp", "email"]
    email_call = provider.send.call_args_list[-1].kwargs
    assert email_call["subject"].startswith("Can we still help?")
    assert email_call["headers"]["List-Unsubscribe"]
