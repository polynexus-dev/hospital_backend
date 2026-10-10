from django.http import JsonResponse

from .service import EXPIRED, EXPIRING_SOON, GRACE_PERIOD, current_status, is_on_premise

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# Reachable whatever the license says: checking and installing a license,
# and signing in to do so.
EXEMPT_PREFIXES = ("/api/v1/licensing/", "/api/v1/auth/")
# Platform-operator surface; meaningless on a single-hospital installation.
SAAS_ONLY_PREFIXES = ("/api/v1/saas-admin/",)


class LicenseMiddleware:
    """On-premise only. A usable license allows everything (with warning
    headers as expiry nears); an expired, tampered, missing or wrong-machine
    license makes the system read-only — reads stay open so clinicians can
    still reach past records."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not is_on_premise():
            return self.get_response(request)
        path = request.path_info
        if path.startswith(SAAS_ONLY_PREFIXES):
            return JsonResponse({"detail": "Not available on an on-premise installation."}, status=404)
        if path.startswith(EXEMPT_PREFIXES):
            return self.get_response(request)

        status = current_status()
        if status.read_only and request.method not in SAFE_METHODS:
            response = JsonResponse(
                {"detail": status.message if status.state != EXPIRED else "License expired. System in read-only emergency archive mode.",
                 "code": f"license_{status.state}"},
                status=402 if status.state == EXPIRED else 403,
            )
        else:
            response = self.get_response(request)

        if status.state != "valid":
            response["X-License-Status"] = status.state
            if status.expires_at:
                response["X-License-Expires-At"] = status.expires_at.isoformat()
            if status.state == GRACE_PERIOD:
                response["X-License-Warning"] = "critical"
            elif status.state == EXPIRING_SOON:
                response["X-License-Warning"] = "expiring"
        return response
