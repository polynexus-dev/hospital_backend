from .models import AuditLog
from .tenancy import reset_current_hospital_id, set_current_hospital_id

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class TenantMiddleware:
    """Resolves the current hospital from the authenticated user and makes
    it available to TenantManager for the duration of the request. Staff
    users may switch tenant via the X-Hospital-Id header (used by internal
    ops tooling / superadmin dashboards that operate across hospitals)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        hospital_id = None
        user = getattr(request, "user", None)

        if user is not None and getattr(user, "is_authenticated", False):
            if user.is_staff and request.headers.get("X-Hospital-Id"):
                hospital_id = request.headers["X-Hospital-Id"]
            elif getattr(user, "hospital_id", None):
                hospital_id = user.hospital_id

        token = set_current_hospital_id(hospital_id)
        try:
            response = self.get_response(request)
        finally:
            reset_current_hospital_id(token)
        return response


class AuditMiddleware:
    """Coarse, always-on audit trail: who hit which endpoint, when, from
    where, with what result. Object-level diffs for sensitive models are
    logged separately via apps.core.audit.log_action."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if request.method in MUTATING_METHODS and not request.path.startswith("/admin/"):
            user = getattr(request, "user", None)
            AuditLog.objects.create(
                hospital_id=getattr(user, "hospital_id", None) if user else None,
                actor=user if user and getattr(user, "is_authenticated", False) else None,
                action=AuditLog.Action.REQUEST,
                method=request.method,
                path=request.path,
                status_code=response.status_code,
                ip_address=self._client_ip(request),
            )
        return response

    @staticmethod
    def _client_ip(request):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")
