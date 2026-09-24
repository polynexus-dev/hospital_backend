from datetime import timedelta

import pytest
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.accounts.models import User
from apps.core.models import AuditLog
from apps.governance import services
from apps.governance.models import Accreditation, AuditRule, BackupRecord, SecurityEvent, SecurityPolicy
from apps.patients.models import Patient

STRONG = "Str0ng!Passw0rd"


@pytest.fixture
def login_user(hospital):
    u = User.objects.create_user(email="nurse@test-hospital.example", password=STRONG, hospital=hospital)
    u.password_changed_at = timezone.now()
    u.save(update_fields=["password_changed_at"])
    return u


def _login(api_client, password, email="nurse@test-hospital.example"):
    return api_client.post("/api/v1/auth/login/", {"email": email, "password": password}, format="json")


# --- DOM.4.a password policy ----------------------------------------------


def test_policy_rejects_weak_passwords(hospital, login_user):
    for weak in ["short1!A", "alllowercase123!", "NoDigitsHere!!", "NoSymbols12345A"]:
        with pytest.raises(ValidationError):
            validate_password(weak, user=login_user)
    validate_password("Another$tr0ngOne", user=login_user)


def test_policy_blocks_password_reuse(hospital, login_user):
    services.record_password_change(login_user)
    with pytest.raises(ValidationError):
        validate_password(STRONG, user=login_user)


def test_expired_password_blocks_login_until_changed(api_client, hospital, login_user):
    SecurityPolicy.for_hospital(hospital)  # defaults: 90 days
    User.objects.filter(pk=login_user.pk).update(password_changed_at=timezone.now() - timedelta(days=91))
    res = _login(api_client, STRONG)
    assert res.status_code == 200 and res.json()["password_expired"] is True
    assert "access" not in res.json()

    res = api_client.post("/api/v1/auth/password/expired-change/", {"email": login_user.email, "old_password": STRONG, "new_password": "Fresh$Passw0rd9"}, format="json")
    assert res.status_code == 200, res.json()
    assert _login(api_client, STRONG).status_code == 401  # old password now denied
    assert "access" in _login(api_client, "Fresh$Passw0rd9").json()


def test_change_password_enforces_policy(auth_client, user):
    user.set_password(STRONG)
    user.save()
    res = auth_client.post("/api/v1/users/change_password/", {"old_password": STRONG, "new_password": "weak"}, format="json")
    assert res.status_code == 400


# --- DOM.4.c lockout / block ------------------------------------------------


def test_account_locks_after_threshold_and_logs_events(api_client, hospital, login_user):
    policy = SecurityPolicy.for_hospital(hospital)
    policy.lockout_threshold = 3
    policy.save()
    for _ in range(2):
        assert _login(api_client, "wrong").status_code == 401
    res = _login(api_client, "wrong")
    assert res.status_code == 401 and res.json()["code"] == "account_locked"
    # correct password is now refused too
    res = _login(api_client, STRONG)
    assert res.status_code == 401 and res.json()["code"] == "account_locked"
    types = set(SecurityEvent.objects.filter(user=login_user).values_list("event_type", flat=True))
    assert {"login_failed", "account_locked", "locked_login_attempt"} <= types


def test_lock_expires(api_client, hospital, login_user):
    User.objects.filter(pk=login_user.pk).update(locked_until=timezone.now() - timedelta(minutes=1), failed_login_attempts=5)
    assert "access" in _login(api_client, STRONG).json()
    login_user.refresh_from_db()
    assert login_user.failed_login_attempts == 0


def test_admin_can_block_and_unblock(auth_client, api_client, hospital, login_user):
    assert auth_client.post(f"/api/v1/users/{login_user.pk}/block/", {"reason": "left org"}, format="json").status_code == 200
    auth_client.force_authenticate(user=None)
    res = _login(api_client, STRONG)
    assert res.status_code == 401 and res.json()["code"] == "account_blocked"


def test_unknown_user_failures_are_logged(api_client, hospital):
    _login(api_client, "x", email="ghost@nowhere.example")
    assert SecurityEvent.objects.filter(username_attempted="ghost@nowhere.example", event_type="login_failed").exists()


# --- DOM.4.b idle lock policy / DOM.3.a ------------------------------------


def test_security_policy_readable_by_all_writable_by_admin(user, restricted_client):
    from rest_framework.test import APIClient

    assert restricted_client.get("/api/v1/governance/security-policy/").json()["idle_lock_minutes"] == 10
    assert restricted_client.patch("/api/v1/governance/security-policy/", {"idle_lock_minutes": 5}, format="json").status_code == 403
    admin = APIClient()
    admin.force_authenticate(user=user)
    res = admin.patch("/api/v1/governance/security-policy/", {"idle_lock_minutes": 5}, format="json")
    assert res.status_code == 200 and res.json()["idle_lock_minutes"] == 5
    assert SecurityEvent.objects.filter(event_type="policy_changed").exists()


def test_forbidden_request_is_logged_as_access_denied(restricted_client, restricted_user):
    res = restricted_client.get("/api/v1/governance/security-events/")
    assert res.status_code == 403
    assert SecurityEvent.objects.filter(user=restricted_user, event_type="access_denied").exists()


def test_screen_unlock(auth_client, user):
    user.set_password(STRONG)
    user.save()
    assert auth_client.post("/api/v1/users/verify-password/", {"password": STRONG}, format="json").status_code == 200
    assert auth_client.post("/api/v1/users/verify-password/", {"password": "nope"}, format="json").status_code == 400
    assert SecurityEvent.objects.filter(event_type="screen_unlock_failed").exists()


# --- DAC.2.c audit rules / DOM.3.b rollback --------------------------------


def test_audit_rules_control_capture(hospital):
    from apps.core.audit import audit_rule_allows

    assert audit_rule_allows(hospital.pk, "Patient", "update")
    assert not audit_rule_allows(hospital.pk, "Patient", "read")
    AuditRule.objects.create(hospital=hospital, name="Patients reads", model_name="Patient", actions=["update"], capture_reads=True)
    assert audit_rule_allows(hospital.pk, "Patient", "read")
    assert audit_rule_allows(hospital.pk, "Patient", "update")
    assert not audit_rule_allows(hospital.pk, "Bill", "update")


def test_rollback_reverts_audited_update(auth_client, hospital, user):
    from apps.bloodbank.models import Donor

    donor = Donor.objects.create(hospital=hospital, name="Asha", blood_group="O+")
    assert auth_client.patch(f"/api/v1/bloodbank/donors/{donor.pk}/", {"name": "Wrong Name"}, format="json").status_code == 200
    entry = AuditLog.objects.filter(model_name="Donor", action="update").latest("created_at")
    res = auth_client.post(f"/api/v1/governance/audit-logs/{entry.pk}/rollback/", format="json")
    assert res.status_code == 200, res.json()
    donor.refresh_from_db()
    assert donor.name == "Asha"


# --- DOM.1.e backup / restore ----------------------------------------------


def test_backup_and_restore_roundtrip(auth_client, hospital, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    patient = Patient.objects.create(hospital=hospital, first_name="Ravi", mobile="9000000001")
    res = auth_client.post("/api/v1/governance/backups/", format="json")
    assert res.status_code == 201 and res.json()["record_count"] >= 1
    Patient.objects.filter(pk=patient.pk).update(first_name="Changed")
    backup_id = res.json()["id"]
    assert auth_client.post(f"/api/v1/governance/backups/{backup_id}/restore/", format="json").status_code == 200
    patient.refresh_from_db()
    assert patient.first_name == "Ravi"


def test_expired_backups_are_purged(hospital, settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    record = services.run_backup(hospital)
    BackupRecord.objects.filter(pk=record.pk).update(expires_at=timezone.now() - timedelta(days=1))
    assert services.purge_expired_backups() == 1
    record.refresh_from_db()
    assert record.status == "expired"


# --- AAC.7.b / DOM.1.b -----------------------------------------------------


def test_public_accreditations_on_login_page(api_client, hospital):
    Accreditation.objects.create(hospital=hospital, name="NABH Full Accreditation", certificate_number="H-2026-001")
    res = api_client.get("/api/v1/governance/public/accreditations/?subdomain=test-hospital")
    assert res.status_code == 200 and res.json()[0]["certificate_number"] == "H-2026-001"


def test_help_centre_search(auth_client):
    auth_client.post("/api/v1/governance/help/", {"title": "Registering a patient", "body": "Open Patients and click New.", "category": "guide"}, format="json")
    res = auth_client.get("/api/v1/governance/help/?search=registering")
    assert any(a["title"] == "Registering a patient" for a in res.json()["results"])
