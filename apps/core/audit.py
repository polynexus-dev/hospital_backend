"""Helper for fine-grained audit entries beyond the coarse per-request log
that AuditMiddleware writes automatically. Call this from a view or
serializer when a change needs an object-level diff on record, e.g. patient
demographic edits or consent changes for DPDP purposes."""
from .models import AuditLog
from .tenancy import get_current_hospital_id


def safe_repr(instance) -> str:
    """How a record is named in the audit log. A model's own __str__ often
    includes encrypted content (a prescription's diagnosis, a patient's
    name), which must not reappear in plain text in the log — such records
    are named by type and id only."""
    from .fields import EncryptedCharField, EncryptedJSONField, EncryptedTextField

    if any(isinstance(f, (EncryptedCharField, EncryptedTextField, EncryptedJSONField)) for f in instance._meta.concrete_fields):
        return f"{instance._meta.verbose_name.capitalize()} #{getattr(instance, 'pk', '')}"[:255]
    return str(instance)[:255]


def audit_rule_allows(hospital_id, model_name, action):
    """NABH DAC.2.c configurable capture rules (apps.governance.AuditRule).
    No active rules for a hospital = capture everything (the safe default).
    Once a hospital defines rules, a fine-grained entry is kept only when
    at least one active rule matches its model and action — except READ,
    which is opt-in per rule via capture_reads, since logging every read of
    every record is only wanted for specific sensitive models."""
    if hospital_id is None:
        return action != "read"
    from apps.governance.models import AuditRule

    rules = list(AuditRule.objects.filter(hospital_id=hospital_id, is_active=True).values("model_name", "actions", "capture_reads"))
    if not rules:
        return action != "read"
    for rule in rules:
        if rule["model_name"] not in ("*", model_name):
            continue
        if action == "read":
            if rule["capture_reads"]:
                return True
            continue
        if not rule["actions"] or action in rule["actions"]:
            return True
    return False


def log_action(*, actor, action, instance, changes=None, request=None, force=False):
    hospital_id = get_current_hospital_id() or getattr(instance, "hospital_id", None)
    model_name = instance.__class__.__name__
    if not force and not audit_rule_allows(hospital_id, model_name, action):
        return None
    return AuditLog.objects.create(
        hospital_id=hospital_id,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        model_name=model_name,
        object_id=str(getattr(instance, "pk", "")),
        object_repr=safe_repr(instance),
        changes=changes or {},
        method=getattr(request, "method", ""),
        path=getattr(request, "path", ""),
        ip_address=_client_ip(request) if request else None,
    )


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
