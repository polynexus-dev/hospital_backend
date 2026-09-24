import gzip
import hashlib
import json
import logging
import os
import re
from datetime import timedelta

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.core import serializers as dj_serializers
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.request_utils import get_client_ip

from .models import BackupRecord, PasswordHistory, RetentionPolicy, SecurityEvent, SecurityPolicy

logger = logging.getLogger(__name__)


# --- Security events (DOM.3.a) ---------------------------------------------

_CRITICAL = {
    SecurityEvent.EventType.ACCOUNT_LOCKED,
    SecurityEvent.EventType.USER_BLOCKED,
    SecurityEvent.EventType.LOCKED_LOGIN_ATTEMPT,
    SecurityEvent.EventType.ROLLBACK,
}
_WARNING = {
    SecurityEvent.EventType.LOGIN_FAILED,
    SecurityEvent.EventType.MFA_FAILED,
    SecurityEvent.EventType.ACCESS_DENIED,
    SecurityEvent.EventType.IP_BLOCKED,
    SecurityEvent.EventType.PASSWORD_EXPIRED,
    SecurityEvent.EventType.SCREEN_UNLOCK_FAILED,
    SecurityEvent.EventType.POLICY_CHANGED,
}


def log_security_event(event_type, *, request=None, user=None, hospital_id=None, username="", details=None):
    if event_type in _CRITICAL:
        severity = SecurityEvent.Severity.CRITICAL
    elif event_type in _WARNING:
        severity = SecurityEvent.Severity.WARNING
    else:
        severity = SecurityEvent.Severity.INFO
    if hospital_id is None and user is not None:
        hospital_id = getattr(user, "hospital_id", None)
    try:
        return SecurityEvent.objects.create(
            hospital_id=hospital_id,
            user=user if getattr(user, "pk", None) else None,
            username_attempted=username or (getattr(user, "email", "") or ""),
            event_type=event_type,
            severity=severity,
            ip_address=get_client_ip(request) if request is not None else None,
            user_agent=(request.META.get("HTTP_USER_AGENT", "")[:300] if request is not None else ""),
            path=(request.path[:512] if request is not None else ""),
            details=details or {},
        )
    except Exception:  # never let security logging break the request it's observing
        logger.exception("Failed to write SecurityEvent %s", event_type)
        return None


# --- Password policy (DOM.4.a) ---------------------------------------------


def password_policy_errors(password, policy):
    errors = []
    if len(password) < policy.password_min_length:
        errors.append(f"Password must be at least {policy.password_min_length} characters long.")
    if policy.password_require_uppercase and not re.search(r"[A-Z]", password):
        errors.append("Password must contain an uppercase letter.")
    if policy.password_require_lowercase and not re.search(r"[a-z]", password):
        errors.append("Password must contain a lowercase letter.")
    if policy.password_require_digit and not re.search(r"\d", password):
        errors.append("Password must contain a digit.")
    if policy.password_require_symbol and not re.search(r"[^A-Za-z0-9]", password):
        errors.append("Password must contain a special character.")
    return errors


class HospitalPasswordPolicyValidator:
    """Django AUTH_PASSWORD_VALIDATORS entry — so every path that sets a
    password through validate_password (admin, change-password, user
    creation serializers) enforces the *hospital's* configured policy."""

    def validate(self, password, user=None):
        policy = SecurityPolicy.for_hospital(getattr(user, "hospital_id", None))
        errors = password_policy_errors(password, policy)
        if user is not None and getattr(user, "pk", None) and policy.password_history_count:
            recent = PasswordHistory.objects.filter(user=user)[: policy.password_history_count]
            if any(check_password(password, h.password_hash) for h in recent):
                errors.append(f"Password cannot be one of your last {policy.password_history_count} passwords.")
        if errors:
            raise ValidationError(errors)

    def get_help_text(self):
        return "Your password must satisfy your hospital's password policy."


def record_password_change(user, *, request=None):
    """Call after user.set_password()+save(): stamps the change time and
    stores the hash for history checks."""
    from apps.accounts.models import User

    now = timezone.now()
    User.objects.filter(pk=user.pk).update(password_changed_at=now)
    user.password_changed_at = now
    PasswordHistory.objects.create(user=user, password_hash=user.password)
    policy = SecurityPolicy.for_hospital(user.hospital_id)
    keep = max(policy.password_history_count, 1)
    stale = PasswordHistory.objects.filter(user=user).values_list("pk", flat=True)[keep:]
    PasswordHistory.objects.filter(pk__in=list(stale)).delete()
    log_security_event(SecurityEvent.EventType.PASSWORD_CHANGED, request=request, user=user)


def password_is_expired(user) -> bool:
    policy = SecurityPolicy.for_hospital(user.hospital_id)
    if not policy.password_expiry_days:
        return False
    changed = user.password_changed_at or user.date_joined
    return changed is not None and changed + timedelta(days=policy.password_expiry_days) < timezone.now()


def password_expires_in_days(user):
    policy = SecurityPolicy.for_hospital(user.hospital_id)
    if not policy.password_expiry_days:
        return None
    changed = user.password_changed_at or user.date_joined
    if changed is None:
        return None
    return (changed + timedelta(days=policy.password_expiry_days) - timezone.now()).days


# --- Lockout (DOM.4.c) -----------------------------------------------------


def register_failed_login(user, *, request=None):
    """Returns True if this failure locked the account."""
    from apps.accounts.models import User

    policy = SecurityPolicy.for_hospital(user.hospital_id)
    with transaction.atomic():
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        locked_user.failed_login_attempts += 1
        fields = ["failed_login_attempts"]
        just_locked = False
        if policy.lockout_threshold and locked_user.failed_login_attempts >= policy.lockout_threshold:
            locked_user.locked_until = timezone.now() + timedelta(minutes=policy.lockout_minutes)
            fields.append("locked_until")
            just_locked = True
        locked_user.save(update_fields=fields)
    log_security_event(
        SecurityEvent.EventType.LOGIN_FAILED, request=request, user=user,
        details={"attempts": locked_user.failed_login_attempts, "threshold": policy.lockout_threshold},
    )
    if just_locked:
        log_security_event(
            SecurityEvent.EventType.ACCOUNT_LOCKED, request=request, user=user,
            details={"locked_until": locked_user.locked_until.isoformat(), "minutes": policy.lockout_minutes},
        )
        _notify_lockout(locked_user)
    return just_locked


def reset_failed_logins(user):
    from apps.accounts.models import User

    User.objects.filter(pk=user.pk).update(failed_login_attempts=0, locked_until=None)


def _notify_lockout(user):
    """Best-effort email to the locked user (DOM.4.c "users are notified").
    Admins see it on the Security Events screen as a critical event."""
    try:
        from django.core.mail import send_mail

        send_mail(
            "Your account has been locked",
            f"Your account was locked after repeated failed sign-in attempts. It will unlock automatically at "
            f"{timezone.localtime(user.locked_until):%d %b %Y %H:%M}, or an administrator can unlock it sooner.",
            getattr(settings, "DEFAULT_FROM_EMAIL", None),
            [user.email],
            fail_silently=True,
        )
    except Exception:
        logger.exception("Lockout notification failed")


# --- Backup / archive (DOM.1.e) --------------------------------------------


def _tenant_models():
    from apps.core.models import TenantScopedModel

    for model in django_apps.get_models():
        if issubclass(model, TenantScopedModel) and not model._meta.abstract and not model._meta.proxy:
            yield model


def backup_dir():
    path = os.path.join(settings.MEDIA_ROOT, "backups")
    os.makedirs(path, exist_ok=True)
    return path


def run_backup(hospital, *, user=None, kind=BackupRecord.Kind.MANUAL):
    """Serializes every tenant-scoped row belonging to `hospital` to a
    gzipped JSON fixture. Per-hospital (not a whole-database dump) so one
    tenant's administrator can back up and restore their own data without
    touching anyone else's — the infrastructure-level Postgres backup is
    the platform's separate, whole-database safety net."""
    policy = RetentionPolicy.for_hospital(hospital)
    record = BackupRecord.objects.create(
        hospital=hospital, kind=kind, triggered_by=user,
        expires_at=timezone.now() + timedelta(days=policy.backup_retention_days),
    )
    try:
        objects, count = [], 0
        for model in _tenant_models():
            qs = model._base_manager.filter(hospital=hospital)
            count += qs.count()
            objects.extend(qs.iterator())
        payload = dj_serializers.serialize("json", objects).encode("utf-8")
        filename = f"{hospital.slug}-{timezone.now():%Y%m%d-%H%M%S}-{record.pk}.json.gz"
        path = os.path.join(backup_dir(), filename)
        with gzip.open(path, "wb") as fh:
            fh.write(payload)
        record.file_path = path
        record.size_bytes = os.path.getsize(path)
        record.record_count = count
        record.checksum_sha256 = hashlib.sha256(payload).hexdigest()
        record.status = BackupRecord.Status.SUCCESS
    except Exception as exc:
        logger.exception("Backup failed for %s", hospital)
        record.status = BackupRecord.Status.FAILED
        record.error = str(exc)[:2000]
    record.finished_at = timezone.now()
    record.save()
    log_security_event(
        SecurityEvent.EventType.BACKUP, user=user, hospital_id=hospital.pk,
        details={"backup_id": record.pk, "status": record.status, "records": record.record_count},
    )
    return record


def restore_backup(record, *, user=None):
    """Re-applies a backup's rows (update-or-insert by primary key). Rows
    created after the backup are left alone — this restores lost/changed
    data, it doesn't wipe newer work."""
    if record.status not in (BackupRecord.Status.SUCCESS, BackupRecord.Status.RESTORED) or not record.file_path:
        raise ValueError("Only a successful backup can be restored.")
    with gzip.open(record.file_path, "rb") as fh:
        payload = fh.read()
    if record.checksum_sha256 and hashlib.sha256(payload).hexdigest() != record.checksum_sha256:
        raise ValueError("Backup file checksum mismatch — file is corrupted or was modified.")
    restored = 0
    with transaction.atomic():
        for obj in dj_serializers.deserialize("json", payload):
            if getattr(obj.object, "hospital_id", None) != record.hospital_id:
                continue
            obj.save()
            restored += 1
    record.status = BackupRecord.Status.RESTORED
    record.save(update_fields=["status"])
    log_security_event(
        SecurityEvent.EventType.ROLLBACK, user=user, hospital_id=record.hospital_id,
        details={"restored_backup_id": record.pk, "rows": restored},
    )
    return restored


def purge_expired_backups(now=None):
    now = now or timezone.now()
    purged = 0
    for record in BackupRecord.objects.filter(expires_at__lt=now).exclude(status=BackupRecord.Status.EXPIRED):
        if record.file_path and os.path.exists(record.file_path):
            os.remove(record.file_path)
        record.status = BackupRecord.Status.EXPIRED
        record.save(update_fields=["status"])
        purged += 1
    return purged


def purge_expired_audit_logs(now=None):
    """DAC.2.c retention — AuditLog rows past the most permissive active
    rule's retention for their hospital are archived to a gzip file, then
    deleted. QuerySet.delete() is used deliberately: AuditLog.delete()
    refuses on a single instance so ordinary code can never remove one,
    and this retention job is the one sanctioned exception."""
    from apps.core.models import AuditLog, Hospital
    from .models import AuditRule

    now = now or timezone.now()
    total = 0
    for hospital in Hospital.objects.all():
        rules = AuditRule.objects.filter(hospital=hospital, is_active=True)
        if not rules.exists():
            continue
        days = max(r.retention_days for r in rules)
        stale = AuditLog.objects.filter(hospital=hospital, created_at__lt=now - timedelta(days=days))
        if not stale.exists():
            continue
        rows = list(stale.values())
        path = os.path.join(backup_dir(), f"auditlog-archive-{hospital.slug}-{now:%Y%m%d%H%M%S}.json.gz")
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            json.dump(rows, fh, default=str)
        total += stale.delete()[0]
    return total


# --- Change rollback (DOM.3.b) ---------------------------------------------


def revert_audited_update(audit_log, *, user=None, request=None):
    """Puts back the `old` values an AuditedModelViewSetMixin update entry
    recorded. Only ever for `update` entries with a field diff, and only
    for fields that still hold the `new` value from that entry (so a later
    legitimate edit is never silently clobbered)."""
    if audit_log.action != "update" or not audit_log.changes:
        raise ValueError("Only field-level update entries can be rolled back.")
    model = next((m for m in django_apps.get_models() if m.__name__ == audit_log.model_name), None)
    if model is None:
        raise ValueError(f"Unknown model {audit_log.model_name}.")
    instance = model._base_manager.filter(pk=audit_log.object_id).first()
    if instance is None:
        raise ValueError("The record no longer exists.")
    if getattr(instance, "hospital_id", audit_log.hospital_id) != audit_log.hospital_id:
        raise ValueError("Record belongs to a different hospital.")

    reverted, skipped = {}, []
    for field_name, diff in audit_log.changes.items():
        field = model._meta.get_field(field_name)
        current = getattr(instance, field.attname)
        if str(current) != diff.get("new"):
            skipped.append(field_name)
            continue
        old = diff.get("old")
        value = None if old in ("None", None) and field.null else field.to_python(old)
        setattr(instance, field.attname, value)
        reverted[field_name] = {"old": str(current), "new": str(value)}
    if not reverted:
        raise ValueError("Nothing to roll back — every field has changed again since this entry.")
    instance.save(update_fields=[model._meta.get_field(f).name for f in reverted])

    from apps.core.audit import log_action

    log_action(actor=user, action="update", instance=instance, changes=reverted, request=request)
    log_security_event(
        SecurityEvent.EventType.ROLLBACK, request=request, user=user, hospital_id=audit_log.hospital_id,
        details={"audit_log_id": audit_log.pk, "model": audit_log.model_name, "object_id": audit_log.object_id, "fields": list(reverted), "skipped": skipped},
    )
    return reverted, skipped
