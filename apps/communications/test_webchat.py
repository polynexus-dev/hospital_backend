"""Website live chat: visitor side (public, signed token) and staff side (Inbox)."""
import json
import time

import pytest
from django.core.cache import cache

from apps.communications.models import Message, Thread
from apps.enquiries.models import Enquiry, WebsiteWidget

SITE = "https://www.swasthyam.example"


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def widget(hospital):
    return WebsiteWidget.objects.create(hospital=hospital, allowed_domains=["swasthyam.example"], chat_enabled=True)


def post(api_client, widget, path, body, origin=SITE):
    return api_client.generic("POST", f"/api/v1/site/{widget.site_key}/chat/{path}/", json.dumps(body), content_type="text/plain;charset=UTF-8",
                              HTTP_ORIGIN=origin, REMOTE_ADDR="203.0.113.9")


def start(api_client, widget, **overrides):
    body = {"name": "Rohit Pawar", "mobile": "9822033445", "message": "Do you have a cardiologist on Sunday?", "consent": True,
            "website": "", "started_at": time.time() * 1000 - 10_000, "utm_source": "google", **overrides}
    return post(api_client, widget, "start", body)


def test_visitor_starts_a_chat_which_becomes_a_website_enquiry(api_client, widget):
    res = start(api_client, widget)
    assert res.status_code == 201, res.json()
    assert [m["from"] for m in res.json()["messages"]] == ["auto", "visitor", "auto"]
    thread = Thread.objects.get(channel="web_chat")
    e = thread.enquiry
    assert (e.name, e.mobile, e.source, e.utm_source) == ("Rohit Pawar", "9822033445", "website", "google") and "Consent given" in e.notes
    assert thread.sla_due_at is not None


# NABH DAC.1.d (another digital channel for patients)
def test_staff_reply_reaches_the_visitor_and_marks_the_enquiry_contacted(api_client, auth_client, user, widget):
    token = start(api_client, widget).json()["token"]
    thread = Thread.objects.get(channel="web_chat")
    inbox = auth_client.get("/api/v1/threads/?channel=web_chat").json()["results"]
    assert [t["id"] for t in inbox] == [thread.pk] and inbox[0]["unread_count"] == 1

    reply = auth_client.post(f"/api/v1/threads/{thread.pk}/reply/", {"body": "Yes, Dr Rao sees patients 10–1 on Sunday."}, format="json")
    assert reply.status_code == 201 and reply.json()["from"] == "staff"
    thread.refresh_from_db()
    assert thread.owner_id == user.pk and thread.sla_due_at is None
    assert Enquiry.objects.get(pk=thread.enquiry_id).stage == "contacted"

    last_seen = max(m["id"] for m in auth_client.get(f"/api/v1/threads/{thread.pk}/chat-messages/").json()["messages"]) - 1
    polled = api_client.get(f"/api/v1/site/{widget.site_key}/chat/messages/?token={token}&after={last_seen}", HTTP_ORIGIN=SITE).json()
    assert polled["messages"][-1]["body"].startswith("Yes, Dr Rao")

    assert post(api_client, widget, "messages", {"token": token, "body": "Great, thanks"}).status_code == 201
    assert Message.objects.filter(channel="web_chat", direction="inbound").count() == 2
    assert auth_client.post(f"/api/v1/threads/{thread.pk}/close/").json() == {"status": "closed"}


def test_a_token_only_opens_its_own_chat(api_client, widget, hospital):
    token = start(api_client, widget).json()["token"]
    other = WebsiteWidget.objects.create(hospital=hospital.__class__.objects.create(name="Other", slug="other-h"), chat_enabled=True)
    assert api_client.get(f"/api/v1/site/{other.site_key}/chat/messages/?token={token}", HTTP_ORIGIN="http://testserver").status_code == 404
    assert api_client.get(f"/api/v1/site/{widget.site_key}/chat/messages/?token=forged", HTTP_ORIGIN=SITE).status_code == 404
    # Browsers leave out Origin on same-site GETs; the token alone authorises polling.
    assert api_client.get(f"/api/v1/site/{widget.site_key}/chat/messages/?token={token}").status_code == 200
    assert api_client.get(f"/api/v1/site/{widget.site_key}/chat/messages/?token={token}", HTTP_ORIGIN="https://copycat.example").status_code == 403


def test_chat_refuses_bots_other_sites_and_missing_consent(api_client, widget):
    assert start(api_client, widget, website="x").status_code == 400
    assert start(api_client, widget, started_at=time.time() * 1000).status_code == 400
    assert set(start(api_client, widget, consent=False, mobile="123").json()) == {"consent", "mobile"}
    assert post(api_client, widget, "start", {}, origin="https://copycat.example").status_code == 403
    assert not Thread.objects.exists()


def test_chat_off_means_no_chat(api_client, widget):
    widget.chat_enabled = False
    widget.save()
    assert start(api_client, widget).status_code == 404


def test_free_text_replies_only_on_website_chats(auth_client, hospital, widget):
    e = Enquiry.objects.create(hospital=hospital, name="WhatsApp lead", mobile="9822000001", source="whatsapp")
    t = Thread.objects.create(hospital=hospital, enquiry=e, channel="whatsapp")
    assert auth_client.post(f"/api/v1/threads/{t.pk}/reply/", {"body": "hi"}, format="json").status_code == 400
