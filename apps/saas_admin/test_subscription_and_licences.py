import json
from datetime import date, timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.licensing import crypto, service
from apps.licensing.tests import FP, keypair, onprem  # noqa: F401 (fixtures)
from apps.saas_admin.models import OnPremiseLicense, TenantInvoice, TenantSubscription


@pytest.fixture
def saas_client(db):
    client = APIClient()
    client.force_authenticate(user=User.objects.create_user(email="owner@platform.example", password="x", is_saas_admin=True))
    return client


# --- the hospital's own subscription (SaaS) -----------------------------------

@pytest.mark.django_db
def test_hospital_admin_sees_own_plan_usage_and_invoices(auth_client, restricted_user, hospital, other_hospital):
    sub = TenantSubscription.objects.create(hospital=hospital, tier="pro", max_staff_users=20, started_at=date(2026, 1, 1),
                                            next_billing_date=date(2026, 11, 1), base_price=12000)
    mine = TenantInvoice.objects.create(hospital=hospital, subscription=sub, invoice_number="INV-T-1", amount=12000,
                                        billing_period_start=date(2026, 9, 1), billing_period_end=date(2026, 9, 30), due_date=date(2026, 10, 7))
    theirs = TenantInvoice.objects.create(hospital=other_hospital, invoice_number="INV-T-2", amount=5000,
                                          billing_period_start=date(2026, 9, 1), billing_period_end=date(2026, 9, 30), due_date=date(2026, 10, 7))

    data = auth_client.get("/api/v1/subscription/").data
    assert data["mode"] == "saas" and data["subscription"]["tier_label"] == "Pro" and data["subscription"]["max_staff_users"] == 20
    assert data["active_users"] >= 2  # the admin and restricted_user
    assert [i["invoice_number"] for i in data["invoices"]] == ["INV-T-1"] and data["outstanding"] == "12000.00"

    assert auth_client.get(f"/api/v1/subscription/invoices/{mine.pk}/pdf/")["Content-Type"] == "application/pdf"
    assert auth_client.get(f"/api/v1/subscription/invoices/{theirs.pk}/pdf/").status_code == 404

    staff = APIClient()
    staff.force_authenticate(user=restricted_user)
    assert staff.get("/api/v1/subscription/").status_code == 403


@pytest.mark.django_db
def test_on_premise_points_to_the_licence(onprem, auth_client):
    assert auth_client.get("/api/v1/subscription/").data == {"mode": "on_premise"}


# --- Licences tab (SaaS console) ----------------------------------------------

@pytest.mark.django_db
def test_licences_list_filters(saas_client, hospital, other_hospital):
    now = timezone.now()

    def lic(lid, h, days, revoked=False):
        return OnPremiseLicense.objects.create(
            hospital=h, license_id=lid, issued_at=now - timedelta(days=300), expires_at=now + timedelta(days=days),
            machine_fingerprint="*", payload={}, license_file="x", revoked_at=now if revoked else None,
        )

    lic("LIC-SOON", hospital, 20)
    lic("LIC-LATER", other_hospital, 200)
    lic("LIC-OLD", hospital, -10)
    lic("LIC-GONE", other_hospital, 100, revoked=True)
    ids = lambda q: [r["license_id"] for r in saas_client.get(f"/api/v1/saas-admin/licenses/{q}").data["results"]]  # noqa: E731

    assert ids("?status=active") == ["LIC-SOON", "LIC-LATER"]  # renewals due first
    assert ids("?status=expired") == ["LIC-OLD"] and ids("?status=revoked") == ["LIC-GONE"]
    assert ids("?expiring_within=60") == ["LIC-SOON"]
    assert ids(f"?search={other_hospital.name[:5]}") == ["LIC-GONE", "LIC-LATER"]  # by expiry date
    soon = saas_client.get("/api/v1/saas-admin/licenses/?status=active").data["results"][0]
    assert 18 <= soon["days_left"] <= 20 and soon["latest_usage"] is None


# --- usage report round trip ------------------------------------------------------

@pytest.mark.django_db
def test_usage_report_round_trip(keypair, onprem, settings, saas_client, auth_client, hospital, monkeypatch):
    # 1. Polynexus issues a licence (SaaS side).
    settings.DEPLOYMENT_MODE = "saas"
    issued = saas_client.post(f"/api/v1/saas-admin/hospitals/{hospital.pk}/generate-license/",
                              {"features": ["hms_core"], "machine_fingerprint": FP, "max_users": 25, "response": "json"}, format="json").data
    record = OnPremiseLicense.objects.get(pk=issued["id"])

    # 2. The hospital installs it and downloads a usage report (on-premise).
    settings.DEPLOYMENT_MODE = "on_premise"
    settings.DEPLOYMENT_ID = record.deployment_id
    service.license_path().write_text(record.license_file)
    cache.clear()
    res = auth_client.get("/api/v1/licensing/usage-report/")
    assert res.status_code == 200 and res["Content-Disposition"].endswith('.json"')
    document = json.loads(res.content)
    report = document["report"]
    assert report["licence"]["license_id"] == record.license_id and report["licence"]["state"] == "valid"
    assert report["usage"]["active_users"] >= 1 and set(report["usage"]) == {
        "active_users", "total_users", "beds", "patients_registered", "patients_registered_last_30_days", "appointments_last_30_days"}

    # 3. Polynexus uploads it in the SaaS console.
    settings.DEPLOYMENT_MODE = "saas"
    up = saas_client.post("/api/v1/saas-admin/licenses/usage-reports/", {"report": res.content.decode()}, format="json")
    assert up.status_code == 201 and up.data["seal_ok"] is True and up.data["active_users"] == report["usage"]["active_users"]
    listed = saas_client.get("/api/v1/saas-admin/licenses/").data["results"][0]
    assert listed["latest_usage"]["seal_ok"] is True

    # An edited report is flagged; a report for a licence we never issued is refused.
    document["report"]["usage"]["active_users"] = 3
    edited = saas_client.post("/api/v1/saas-admin/licenses/usage-reports/", {"report": json.dumps(document)}, format="json")
    assert edited.status_code == 201 and edited.data["seal_ok"] is False
    document["report"]["licence"]["license_id"] = "LIC-NOPE"
    assert saas_client.post("/api/v1/saas-admin/licenses/usage-reports/", {"report": json.dumps(document)}, format="json").status_code == 400
