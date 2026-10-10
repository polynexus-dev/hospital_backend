from datetime import timedelta
from io import StringIO

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.licensing import crypto, public_key, service
from apps.licensing.models import LicenseClock

FP = "a" * 64


@pytest.fixture
def keypair(monkeypatch, settings):
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(public_key, "PUBLIC_KEY_PEM", crypto.public_pem(key))
    settings.LICENSE_SIGNING_KEY = crypto.private_pem(key)
    return key


@pytest.fixture
def onprem(settings, tmp_path, keypair, monkeypatch):
    settings.DEPLOYMENT_MODE = "on_premise"
    settings.LICENSE_FILE_PATH = str(tmp_path / "hospital.lic")
    monkeypatch.setattr(service, "machine_fingerprint", lambda: FP)
    cache.clear()
    yield settings
    cache.clear()


def make_payload(hospital, *, days=365, fingerprint=FP, grace=14, issued_days_ago=0, **extra):
    now = timezone.now()
    return {
        "license_id": "LIC-TEST-001",
        "hospital_id": str(hospital.pk),
        "hospital_name": hospital.name,
        "issued_at": (now - timedelta(days=issued_days_ago)).isoformat(),
        "expires_at": (now + timedelta(days=days)).isoformat(),
        "grace_period_days": grace,
        "tier": "enterprise",
        "enabled_modules": [],
        "max_active_users": 0,
        "max_beds": 0,
        "machine_fingerprint": fingerprint,
        **extra,
    }


def install(keypair, payload):
    service.license_path().write_text(crypto.seal(payload, keypair))
    cache.clear()


# --- signing ---------------------------------------------------------------

def test_seal_round_trip_and_tamper_detection():
    key, other = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    blob = crypto.seal({"a": 1}, key)
    assert crypto.open_license(blob, key.public_key()) == {"a": 1}
    with pytest.raises(crypto.LicenseTampered):
        crypto.open_license(blob, other.public_key())  # signed by someone else
    with pytest.raises(crypto.LicenseTampered):
        crypto.open_license(blob[:-8] + "AAAAAAA=", key.public_key())
    with pytest.raises(crypto.LicenseTampered):
        crypto.open_license("not a license", key.public_key())


# --- statuses --------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize("days, state", [
    (365, service.VALID),
    (10, service.EXPIRING_SOON),
    (-5, service.GRACE_PERIOD),
    (-30, service.EXPIRED),
])
def test_expiry_states(onprem, keypair, hospital, days, state):
    assert service.verify_license(crypto.seal(make_payload(hospital, days=days, issued_days_ago=400), keypair)).state == state


@pytest.mark.django_db
def test_machine_binding_and_wildcard(onprem, keypair, hospital):
    assert service.verify_license(crypto.seal(make_payload(hospital, fingerprint="b" * 64), keypair)).state == service.INVALID_MACHINE
    assert service.verify_license(crypto.seal(make_payload(hospital, fingerprint="*"), keypair)).state == service.VALID


@pytest.mark.django_db
def test_missing_foreign_hospital_and_forged(onprem, keypair, hospital, other_hospital):
    assert service.verify_license().state == service.MISSING
    other = make_payload(hospital)
    other["hospital_id"] = "00000000-0000-0000-0000-000000000000"
    assert service.verify_license(crypto.seal(other, keypair)).state == service.TAMPERED
    forged = crypto.seal(make_payload(hospital), Ed25519PrivateKey.generate())
    assert service.verify_license(forged).state == service.TAMPERED


@pytest.mark.django_db
def test_clock_rollback_is_detected(onprem, keypair, hospital):
    blob = crypto.seal(make_payload(hospital, issued_days_ago=100), keypair)
    assert service.verify_license(blob).state == service.VALID
    LicenseClock.objects.update(last_seen_at=timezone.now() + timedelta(days=30))  # we've been later than "now" before
    status = service.verify_license(blob)
    assert status.state == service.TAMPERED and "clock" in status.message


# --- middleware ------------------------------------------------------------

@pytest.mark.django_db
def test_saas_mode_ignores_licensing(auth_client, settings):
    settings.DEPLOYMENT_MODE = "saas"
    assert auth_client.get("/api/v1/licensing/status/").data == {"mode": "saas"}
    assert auth_client.post("/api/v1/departments/", {"name": "Cardiology"}, format="json").status_code == 201


@pytest.mark.django_db
def test_expired_license_is_read_only(onprem, keypair, auth_client, hospital):
    install(keypair, make_payload(hospital, days=-30, issued_days_ago=400))
    res = auth_client.post("/api/v1/departments/", {"name": "Cardiology"}, format="json")
    assert res.status_code == 402 and "read-only" in res.json()["detail"]
    res = auth_client.get("/api/v1/departments/")
    assert res.status_code == 200 and res["X-License-Status"] == "expired"  # records stay readable
    assert APIClient().post("/api/v1/auth/login/", {"email": "x@y.z", "password": "nope"}, format="json").status_code == 401  # login still reachable


@pytest.mark.django_db
def test_grace_period_and_expiring_allow_writes_with_headers(onprem, keypair, auth_client, hospital):
    install(keypair, make_payload(hospital, days=-3, issued_days_ago=400))
    res = auth_client.post("/api/v1/departments/", {"name": "Cardiology"}, format="json")
    assert res.status_code == 201 and res["X-License-Status"] == "grace_period" and res["X-License-Warning"] == "critical"

    install(keypair, make_payload(hospital, days=10))
    res = auth_client.get("/api/v1/departments/")
    assert res["X-License-Status"] == "expiring_soon" and res["X-License-Expires-At"]


@pytest.mark.django_db
def test_saas_admin_api_is_gone_on_premise(onprem, auth_client):
    assert auth_client.get("/api/v1/saas-admin/hospitals/").status_code == 404


# --- upload / status -------------------------------------------------------

@pytest.mark.django_db
def test_admin_uploads_license(onprem, keypair, auth_client, restricted_user, hospital):
    blob = crypto.seal(make_payload(hospital), keypair)
    staff = APIClient()  # its own client: auth_client shares the default one
    staff.force_authenticate(user=restricted_user)
    assert staff.post("/api/v1/licensing/upload/", {"license": blob}, format="json").status_code == 403

    res = auth_client.post("/api/v1/licensing/upload/", {"license": blob}, format="json")
    assert res.status_code == 200 and res.data["state"] == "valid" and res.data["license_id"] == "LIC-TEST-001"
    assert auth_client.get("/api/v1/licensing/status/").data["machine_fingerprint"] == FP

    older = crypto.seal(make_payload(hospital, issued_days_ago=10, license_id="LIC-OLD"), keypair)
    assert auth_client.post("/api/v1/licensing/upload/", {"license": older}, format="json").status_code == 400
    bad = crypto.seal(make_payload(hospital, fingerprint="c" * 64), keypair)
    assert auth_client.post("/api/v1/licensing/upload/", {"license": bad}, format="json").data["state"] == "invalid_machine"


# --- licensed limits -------------------------------------------------------

@pytest.mark.django_db
def test_user_cap(onprem, keypair, auth_client, hospital, user):
    install(keypair, make_payload(hospital, max_active_users=1))  # `user` already uses the one seat
    res = auth_client.post("/api/v1/users/", {"email": "new@test-hospital.example", "password": "Str0ng!Passw0rd#"}, format="json")
    assert res.status_code == 400 and "allows 1 active users" in res.data["detail"]


@pytest.mark.django_db
def test_licensed_modules_limit_the_hospital(onprem, keypair, hospital):
    from apps.core.modules import is_enabled

    install(keypair, make_payload(hospital, enabled_modules=["opd", "pharmacy"]))
    assert is_enabled(hospital, "opd") and not is_enabled(hospital, "icu")


# --- SaaS side: issuing ----------------------------------------------------

@pytest.fixture
def saas_client(db):
    client = APIClient()
    client.force_authenticate(user=User.objects.create_user(email="owner@platform.example", password="x", is_saas_admin=True))
    return client


@pytest.mark.django_db
def test_saas_issues_downloads_and_revokes(keypair, saas_client, hospital):
    url = f"/api/v1/saas-admin/hospitals/{hospital.pk}/generate-license/"
    body = {"duration_days": 365, "modules": ["opd", "ipd"], "machine_fingerprint": FP.upper(), "max_users": 50, "max_beds": 100}
    res = saas_client.post(url, body, format="json")
    assert res.status_code == 200 and res["Content-Disposition"].endswith('.lic"')

    payload = crypto.open_license(res.content.decode(), keypair.public_key())
    assert payload["hospital_id"] == str(hospital.pk) and payload["max_beds"] == 100 and payload["machine_fingerprint"] == FP
    hospital.refresh_from_db()
    assert hospital.is_on_premise

    record = saas_client.post(url, {**body, "response": "json"}, format="json").data
    assert record["license_id"].endswith("-002") and record["status"] == "active"
    assert saas_client.post(f"/api/v1/saas-admin/licenses/{record['id']}/revoke/", {"reason": "replaced"}, format="json").data["status"] == "revoked"
    assert saas_client.get(f"/api/v1/saas-admin/licenses/{record['id']}/download/").status_code == 410
    assert saas_client.post(url, {**body, "machine_fingerprint": "abc"}, format="json").status_code == 400


@pytest.mark.django_db
def test_issuing_without_signing_key(settings, saas_client, hospital):
    settings.LICENSE_SIGNING_KEY = settings.LICENSE_SIGNING_KEY_PATH = ""
    res = saas_client.post(f"/api/v1/saas-admin/hospitals/{hospital.pk}/generate-license/",
                           {"modules": ["opd"], "machine_fingerprint": "*"}, format="json")
    assert res.status_code == 503


# --- boot & setup ----------------------------------------------------------

@pytest.mark.django_db
def test_boot_refuses_wrong_machine(onprem, keypair, hospital):
    install(keypair, make_payload(hospital, fingerprint="d" * 64))
    with pytest.raises(RuntimeError, match="different server"):
        service.check_machine_binding()
    install(keypair, make_payload(hospital, days=-30, issued_days_ago=400))
    service.check_machine_binding()  # expired still boots, read-only


@pytest.mark.django_db
def test_setup_onprem_creates_licensed_hospital(onprem, keypair, tmp_path):
    import uuid

    from apps.core.models import Hospital

    payload = make_payload(Hospital(pk=uuid.uuid4(), name="Apollo Clinic Mumbai"), enabled_modules=["opd", "ipd"])
    lic = tmp_path / "new.lic"
    lic.write_text(crypto.seal(payload, keypair))
    call_command("setup_onprem", license=str(lic), admin_email="owner@apollo.example", admin_password="Str0ng!Passw0rd#", stdout=StringIO())

    hospital = Hospital.objects.get(pk=payload["hospital_id"])
    assert hospital.is_on_premise and hospital.enabled_modules == ["opd", "ipd"]
    owner = User.objects.get(email="owner@apollo.example")
    assert owner.hospital_id == hospital.pk and owner.role.template == Role.Template.OWNER
    assert service.current_status().state == service.VALID
