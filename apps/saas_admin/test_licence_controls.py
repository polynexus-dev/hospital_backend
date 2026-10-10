"""The controls that stop unauthorised licences (see licence_controls.py)."""
import json

import pytest
from django.conf import settings
from django.core import mail
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import User
from apps.governance.models import SecurityEvent
from apps.licensing import crypto, service
from apps.licensing.tests import FP, keypair, make_saas_client, onprem  # noqa: F401 (fixtures)
from apps.saas_admin.models import LicenseRequest, OnPremiseLicense

TERMS = {"duration_days": 365, "features": ["hms_core"], "machine_fingerprint": FP, "max_users": 20}


@pytest.fixture
def owner(db, keypair):
    return make_saas_client("owner@polynexus.example")


@pytest.fixture
def platform_admin(db):
    return make_saas_client("padmin@polynexus.example", saas_role=User.SaaSRole.PLATFORM_ADMIN)


def gen_url(hospital):
    return f"/api/v1/saas-admin/hospitals/{hospital.pk}/generate-license/"


def grant(owner, staff_client, on=True):
    res = owner.post(f"/api/v1/saas-admin/staff/{staff_client.user.pk}/licence-right/", {"grant": on, "otp": owner.otp()}, format="json")
    staff_client.user.refresh_from_db()  # force_authenticate keeps the object; a real request reloads it
    return res


# --- 1 & 2: only Owners, and staff an Owner authorised -------------------------

@pytest.mark.django_db
def test_platform_admins_cannot_issue_until_an_owner_grants_it(owner, platform_admin, hospital):
    assert platform_admin.post(gen_url(hospital), {**TERMS, "otp": platform_admin.otp()}, format="json").status_code == 403
    # Only an Owner can grant the right — not the admin themself.
    assert platform_admin.post(f"/api/v1/saas-admin/staff/{platform_admin.user.pk}/licence-right/", {"grant": True, "otp": platform_admin.otp()}, format="json").status_code == 403
    assert grant(owner, platform_admin).data["can_issue_licenses"] is True
    assert platform_admin.post(gen_url(hospital), {**TERMS, "otp": platform_admin.otp()}, format="json").status_code == 202


# --- 3: a fresh 2FA code, and 2FA must be on -------------------------------------

@pytest.mark.django_db
def test_issuing_needs_a_current_2fa_code(owner, hospital):
    assert owner.post(gen_url(hospital), {**TERMS, "otp": "000000"}, format="json").data["otp"]
    no_2fa = APIClient()
    no_2fa.force_authenticate(user=User.objects.create_user(email="o2@polynexus.example", password="x", is_saas_admin=True))
    assert "Turn on two-factor" in no_2fa.post(gen_url(hospital), {**TERMS, "otp": "123456"}, format="json").data["otp"][0]
    assert not OnPremiseLicense.objects.exists()


# --- 4, 5, 6: two-person approval, staff codes signed in, owners notified ------

@pytest.mark.django_db
def test_request_then_owner_approval_signs_both_staff_codes(owner, platform_admin, hospital):
    grant(owner, platform_admin)
    mail.outbox.clear()
    res = platform_admin.post(gen_url(hospital), {**TERMS, "otp": platform_admin.otp()}, format="json")
    assert res.status_code == 202 and not OnPremiseLicense.objects.exists()  # nothing signed yet
    assert mail.outbox and "Approval needed" in mail.outbox[0].subject and owner.user.email in mail.outbox[0].to
    req_id = res.data["request"]["id"]

    # The requester can't approve their own request.
    assert platform_admin.post(f"/api/v1/saas-admin/license-requests/{req_id}/approve/", {"otp": platform_admin.otp()}, format="json").status_code == 403
    assert owner.post(f"/api/v1/saas-admin/license-requests/{req_id}/approve/", {"otp": "000000"}, format="json").status_code == 400

    approved = owner.post(f"/api/v1/saas-admin/license-requests/{req_id}/approve/", {"otp": owner.otp()}, format="json")
    assert approved.status_code == 200 and approved.data["request"]["status"] == "approved"
    record = OnPremiseLicense.objects.get()
    payload = crypto.open_license(record.license_file, crypto.load_private_key(settings.LICENSE_SIGNING_KEY).public_key())
    assert payload["issued_by"] == platform_admin.user.staff_code and payload["approved_by"] == owner.user.staff_code
    assert any("Licence issued" in m.subject for m in mail.outbox)

    listed = owner.get(f"/api/v1/saas-admin/licenses/?issued_by={platform_admin.user.staff_code}").data["results"]
    assert len(listed) == 1 and listed[0]["approved_by_code"] == owner.user.staff_code and listed[0]["paid"] is False
    assert SecurityEvent.objects.filter(event_type="license_issued", details__action="issued").exists()


@pytest.mark.django_db
def test_managers_see_only_their_own_requests(owner, platform_admin, hospital):
    other = make_saas_client("other@polynexus.example", saas_role=User.SaaSRole.BILLING)
    grant(owner, platform_admin)
    grant(owner, other)
    platform_admin.post(gen_url(hospital), {**TERMS, "otp": platform_admin.otp()}, format="json")
    assert other.get("/api/v1/saas-admin/license-requests/").data["count"] == 0
    assert owner.get("/api/v1/saas-admin/license-requests/?status=pending").data["count"] == 1


# --- leaving: block ends access at once ----------------------------------------------

@pytest.mark.django_db
def test_blocking_staff_ends_sessions_and_rights_immediately(owner, platform_admin, hospital):
    grant(owner, platform_admin)
    platform_admin.post(gen_url(hospital), {**TERMS, "otp": platform_admin.otp()}, format="json")
    refresh = RefreshToken.for_user(platform_admin.user)
    live = APIClient()
    live.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    assert live.get("/api/v1/saas-admin/licenses/").status_code == 200

    res = owner.post(f"/api/v1/saas-admin/staff/{platform_admin.user.pk}/block/", {"reason": "Left the company", "otp": owner.otp()}, format="json")
    assert res.data["is_blocked"] is True and res.data["can_issue_licenses"] is False
    assert live.get("/api/v1/saas-admin/licenses/").status_code == 401  # the still-valid token is refused now
    assert APIClient().post("/api/v1/auth/refresh/", {"refresh": str(refresh)}, format="json").status_code == 401
    assert LicenseRequest.objects.get().status == "cancelled"


# --- 8: revocation list in each release ---------------------------------------------

@pytest.mark.django_db
def test_revoked_licences_stop_once_the_new_list_ships(owner, onprem, settings, hospital, tmp_path, monkeypatch):
    settings.DEPLOYMENT_MODE = "saas"
    record_data = owner.post(gen_url(hospital), {**TERMS, "otp": owner.otp(), "response": "json"}, format="json").data
    record = OnPremiseLicense.objects.get(pk=record_data["id"])
    owner.post(f"/api/v1/saas-admin/licenses/{record.pk}/revoke/", {"reason": "sold without approval"}, format="json")
    revocations = owner.get("/api/v1/saas-admin/licenses/revocation-list/").content.decode()

    settings.DEPLOYMENT_MODE = "on_premise"
    settings.DEPLOYMENT_ID = record.deployment_id
    monkeypatch.setattr(service, "REVOCATIONS_FILE", tmp_path / "revocations.lic")
    assert service.verify_license(record.license_file).state == service.VALID  # old release: no list yet
    (tmp_path / "revocations.lic").write_text(revocations)
    assert service.verify_license(record.license_file).state == service.REVOKED
    (tmp_path / "revocations.lic").write_text(revocations[:-10] + "AAAAAAAA==")  # an edited list is ignored, not trusted
    assert service.verify_license(record.license_file).state == service.VALID


# --- 9: a licence we never issued raises an alert -------------------------------------

@pytest.mark.django_db
def test_usage_report_for_an_unknown_licence_alerts_owners(owner, hospital):
    mail.outbox.clear()
    rogue = {"report": {"licence": {"license_id": "LIC-ROGUE-1", "issued_by": "PNX-0042"}, "generated_at": "2026-10-10T10:00:00+00:00",
                        "usage": {"active_users": 30}, "hospitals": ["Some Clinic"]}, "seal": "x"}
    res = owner.post("/api/v1/saas-admin/licenses/usage-reports/", {"report": json.dumps(rogue)}, format="json")
    assert res.status_code == 400 and "ALERT" in res.data["report"][0]
    event = SecurityEvent.objects.get(event_type="license_alert")
    assert event.severity == "critical" and event.details["issued_by_in_file"] == "PNX-0042"
    assert any("unknown licence" in m.subject for m in mail.outbox)


@pytest.mark.django_db
def test_staff_get_a_permanent_code(db):
    staff = make_saas_client("new@polynexus.example").user
    staff.refresh_from_db()
    assert staff.staff_code == f"PNX-{staff.pk:04d}"
    hospital_user = User.objects.create_user(email="doc@hospital.example", password="x")
    assert hospital_user.staff_code is None
