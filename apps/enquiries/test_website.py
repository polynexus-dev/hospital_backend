"""Website enquiry form added to a hospital's own site with one script tag."""
import json
import time

import pytest
from django.core.cache import cache

from apps.enquiries.models import Enquiry, WebsiteWidget

SITE = "https://www.swasthyam.example"


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def widget(auth_client, hospital):
    res = auth_client.patch("/api/v1/enquiries/website-widget/", {"allowed_domains": ["swasthyam.example"], "services": ["Cardiology", "Orthopaedics"]}, format="json")
    assert res.status_code == 200, res.json()
    return WebsiteWidget.objects.get(hospital=hospital)


def submit(api_client, widget, origin=SITE, **overrides):
    body = {"name": "Kavita Deshmukh", "mobile": "+91 98220 11223", "email": "kavita@example.com", "service": "Cardiology",
            "message": "Chest pain on climbing stairs", "consent": True, "website": "", "started_at": time.time() * 1000 - 20_000,
            "utm_source": "google", "utm_medium": "cpc", "utm_campaign": "cardiac-oct", "gclid": "abc123",
            "landing_page": f"{SITE}/cardiology?utm_source=google", **overrides}
    return api_client.generic("POST", f"/api/v1/site/{widget.site_key}/enquiry/", json.dumps(body), content_type="text/plain;charset=UTF-8",
                              HTTP_ORIGIN=origin, REMOTE_ADDR="203.0.113.7")


def test_admin_gets_embed_code_and_cleans_domains(auth_client, widget):
    data = auth_client.patch("/api/v1/enquiries/website-widget/", {"allowed_domains": "https://www.swasthyam.example/contact, swasthyam.in"}, format="json").json()
    assert data["allowed_domains"] == ["www.swasthyam.example", "swasthyam.in"]
    assert f"/api/v1/site/{widget.site_key}/widget.js" in data["embed_code"] and "data-polynexus-enquiry" in data["embed_code"]
    assert auth_client.patch("/api/v1/enquiries/website-widget/", {"accent_color": "teal"}, format="json").status_code == 400


def test_widget_script_and_config_are_public(api_client, widget):
    js = api_client.get(f"/api/v1/site/{widget.site_key}/widget.js")
    assert js.status_code == 200 and js["Content-Type"].startswith("application/javascript")
    assert f"/api/v1/site/{widget.site_key}/" in js.content.decode() and "__SITE_API__" not in js.content.decode()
    cfg = api_client.get(f"/api/v1/site/{widget.site_key}/config/", HTTP_ORIGIN=SITE)
    assert cfg.json()["services"] == ["Cardiology", "Orthopaedics"] and cfg["Access-Control-Allow-Origin"] == SITE
    assert "Test Hospital" in cfg.json()["consent_text"]
    assert api_client.get("/api/v1/site/not-a-key/config/").status_code == 404


def test_website_enquiry_lands_in_the_crm_with_its_campaign(api_client, hospital, widget):
    res = submit(api_client, widget)
    assert res.status_code == 201, res.json()
    assert res["Access-Control-Allow-Origin"] == SITE
    e = Enquiry.objects.get(pk=res.json()["id"])
    assert (e.name, e.mobile, e.source, e.service_requested) == ("Kavita Deshmukh", "9822011223", "website", "Cardiology")
    assert (e.utm_source, e.utm_medium, e.utm_campaign) == ("google", "cpc", "cardiac-oct")
    assert "Consent given on the website form" in e.notes and "gclid: abc123" in e.notes and "Chest pain" in e.notes


def test_other_websites_and_bad_input_are_refused(api_client, widget):
    assert submit(api_client, widget, origin="https://copycat.example").status_code == 403
    errors = submit(api_client, widget, mobile="12345", consent=False).json()
    assert set(errors) == {"mobile", "consent"}
    assert not Enquiry.objects.exists()


def test_bots_are_silently_dropped(api_client, widget):
    assert submit(api_client, widget, website="http://spam.example").status_code == 201  # honeypot filled
    assert submit(api_client, widget, started_at=time.time() * 1000).status_code == 201  # sent in under 3 seconds
    assert not Enquiry.objects.exists()


def test_repeat_submissions_are_rate_limited(api_client, widget):
    codes = [submit(api_client, widget, name=f"Person {i}").status_code for i in range(4)]
    assert codes == [201, 201, 201, 429]  # same mobile: 3 a day
    assert Enquiry.objects.count() == 3


def test_disabled_form_and_rotated_key_stop_working(auth_client, api_client, widget):
    old_key = widget.site_key
    new_key = auth_client.post("/api/v1/enquiries/website-widget/").json()["site_key"]
    assert new_key != old_key
    assert api_client.get(f"/api/v1/site/{old_key}/config/").status_code == 404
    auth_client.patch("/api/v1/enquiries/website-widget/", {"form_enabled": False}, format="json")
    widget.refresh_from_db()
    assert submit(api_client, widget).status_code == 404


def test_staff_without_enquiry_rights_cannot_change_the_widget(restricted_client):
    assert restricted_client.get("/api/v1/enquiries/website-widget/").status_code == 403
