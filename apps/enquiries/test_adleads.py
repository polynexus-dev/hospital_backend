"""Leads straight from Meta lead ads and Google Ads lead forms. The Graph API is
mocked; signatures and keys are checked for real."""
import hashlib
import hmac
import json
from unittest import mock

import pytest

from apps.enquiries.models import AdLead, AdLeadIntegration, Enquiry

APP_SECRET = "meta-app-secret-123"


@pytest.fixture
def ads(auth_client, hospital):
    res = auth_client.patch("/api/v1/enquiries/ad-leads/", {"meta_app_secret": APP_SECRET, "meta_page_token": "EAAG-page-token", "meta_page_id": "1122",
                                                           "meta_enabled": True, "google_enabled": True}, format="json")
    assert res.status_code == 200, res.json()
    return AdLeadIntegration.objects.get(hospital=hospital)


def meta_post(api_client, hospital, body, secret=APP_SECRET):
    raw = json.dumps(body).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return api_client.generic("POST", f"/api/v1/leads/meta/{hospital.pk}/", raw, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=sig)


def leadgen(lead_id="L-900", page_id="1122"):
    return {"object": "page", "entry": [{"id": page_id, "changes": [{"field": "leadgen", "value": {"leadgen_id": lead_id, "page_id": page_id, "form_id": "F-1"}}]}]}


def graph_lead():
    resp = mock.Mock(status_code=200)
    resp.json.return_value = {
        "field_data": [{"name": "full_name", "values": ["Sunil Gawande"]}, {"name": "phone_number", "values": ["+919822044556"]},
                       {"name": "email", "values": ["sunil@example.com"]}, {"name": "which_treatment_are_you_looking_for?", "values": ["Knee replacement"]},
                       {"name": "city", "values": ["Nagpur"]}],
        "campaign_name": "Knee Oct 2026", "ad_name": "Knee video", "form_id": "F-1", "platform": "ig",
    }
    return mock.patch("apps.enquiries.adleads.requests.get", return_value=resp)


def test_settings_keep_secrets_hidden(auth_client, ads):
    data = auth_client.get("/api/v1/enquiries/ad-leads/").json()
    assert data["meta"]["app_secret_set"] and data["meta"]["page_token_set"] and "meta_app_secret" not in json.dumps(data)
    assert data["meta"]["webhook_url"].endswith(f"/api/v1/leads/meta/{ads.hospital_id}/") and data["google"]["key"] == ads.google_key


def test_meta_subscription_check(api_client, hospital, ads):
    url = f"/api/v1/leads/meta/{hospital.pk}/"
    ok = api_client.get(url, {"hub.mode": "subscribe", "hub.verify_token": ads.meta_verify_token, "hub.challenge": "1158201444"})
    assert ok.status_code == 200 and ok.content == b"1158201444"
    assert api_client.get(url, {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "x"}).status_code == 403


def test_meta_lead_becomes_an_enquiry_once(api_client, hospital, ads):
    with graph_lead() as get:
        assert meta_post(api_client, hospital, leadgen()).json() == {"received": True, "created": 1}
        assert meta_post(api_client, hospital, leadgen()).json()["created"] == 0  # Meta retried
    assert get.call_count == 1 and get.call_args.kwargs["params"]["access_token"] == "EAAG-page-token"
    e = Enquiry.objects.get()
    assert (e.name, e.mobile, e.email, e.source) == ("Sunil Gawande", "9822044556", "sunil@example.com", "meta")
    assert (e.service_requested, e.campaign, e.utm_source, e.utm_medium) == ("Knee replacement", "Knee Oct 2026", "instagram", "paid_social")
    assert "City: Nagpur" in e.notes and "lead id L-900" in e.notes


def test_meta_rejects_forged_and_other_pages(api_client, hospital, ads):
    with graph_lead() as get:
        assert meta_post(api_client, hospital, leadgen(), secret="forged").status_code == 403
        assert meta_post(api_client, hospital, leadgen(page_id="9999")).json()["created"] == 0
    assert get.call_count == 0 and not Enquiry.objects.exists()


def test_failed_meta_fetch_is_logged_and_can_be_retried(auth_client, api_client, hospital, ads):
    bad = mock.Mock(status_code=400)
    bad.json.return_value = {"error": {"message": "Error validating access token: Session has expired"}}
    with mock.patch("apps.enquiries.adleads.requests.get", return_value=bad):
        meta_post(api_client, hospital, leadgen("L-77"))
    row = AdLead.objects.get()
    assert row.status == "failed" and "expired" in row.error and not Enquiry.objects.exists()
    with graph_lead():
        recent = auth_client.post("/api/v1/enquiries/ad-leads/", {"retry": row.pk}, format="json").json()["recent"]
    assert recent[0]["status"] == "created" and Enquiry.objects.count() == 1


def google_lead(key, **overrides):
    return {"lead_id": "G-123", "api_version": "1.0", "form_id": 45, "campaign_id": 678, "google_key": key, "is_test": False, "gcl_id": "gclid-1",
            "user_column_data": [{"column_id": "FULL_NAME", "string_value": "Anita Bhoyar"}, {"column_id": "PHONE_NUMBER", "string_value": "+91 98220 55667"},
                                 {"column_id": "EMAIL", "string_value": "anita@example.com"}, {"column_name": "Which department?", "string_value": "Gynaecology"}],
            **overrides}


def test_google_lead_becomes_an_enquiry_once(api_client, hospital, ads):
    url = f"/api/v1/leads/google/{hospital.pk}/"
    assert api_client.post(url, google_lead("wrong-key"), format="json").status_code == 403
    assert api_client.post(url, google_lead(ads.google_key), format="json").status_code == 200
    assert api_client.post(url, google_lead(ads.google_key), format="json").status_code == 200  # retried
    e = Enquiry.objects.get()
    assert (e.name, e.mobile, e.source, e.service_requested) == ("Anita Bhoyar", "9822055667", "google", "Gynaecology")
    assert e.campaign == "Google Ads campaign 678" and "gclid: gclid-1" in e.notes


def test_google_test_lead_is_marked(api_client, hospital, ads):
    api_client.post(f"/api/v1/leads/google/{hospital.pk}/", google_lead(ads.google_key, lead_id="T-1", is_test=True), format="json")
    assert AdLead.objects.get().status == "test" and Enquiry.objects.get().notes.startswith("TEST lead")


def test_meta_needs_secrets_before_switching_on(auth_client):
    assert auth_client.patch("/api/v1/enquiries/ad-leads/", {"meta_enabled": True}, format="json").status_code == 400
