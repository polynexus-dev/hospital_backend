"""Helper for fine-grained audit entries beyond the coarse per-request log
that AuditMiddleware writes automatically. Call this from a view or
serializer when a change needs an object-level diff on record, e.g. patient
demographic edits or consent changes for DPDP purposes."""
from .models import AuditLog


def log_action(*, actor, action, instance, changes=None, request=None):
    # NOT apps.core.tenancy.get_current_hospital_id() — that contextvar is
    # populated by TenantMiddleware from request.user *before* DRF resolves
    # JWT authentication (see TenantScopedViewSetMixin's docstring for the
    # same bug, already fixed there the same way): for real API traffic it's
    # always unset at that point, so every call here would silently write
    # hospital=None. `instance` is always a TenantScopedModel — its own
    # hospital_id is the correct, already-resolved source.
    hospital_id = getattr(instance, "hospital_id", None) or getattr(actor, "hospital_id", None)
    AuditLog.objects.create(
        hospital_id=hospital_id,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        model_name=instance.__class__.__name__,
        object_id=str(getattr(instance, "pk", "")),
        object_repr=str(instance)[:255],
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
