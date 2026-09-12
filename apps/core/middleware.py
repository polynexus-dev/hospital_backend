import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

from .models import AuditLog
from .payload_crypto import decrypt_payload, encrypt_payload
from .request_utils import get_client_ip
from .tenancy import reset_current_hospital_id, set_current_hospital_id

logger = logging.getLogger(__name__)

_SESSION_CACHE_PREFIX = "payload_enc_session:"

# Endpoints that must NOT be encrypted (handshake + auth bootstrap).
_ENCRYPTION_BYPASS_PATHS = frozenset([
    "/api/v1/session-key/",
    "/api/v1/auth/login/",
    "/api/v1/auth/refresh/",
    "/api/v1/auth/logout/",
    "/api/schema/",
    "/api/schema/swagger-ui/",
    "/admin/",
])


MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Part A #6 — "every read of a patient record is logged". Scoped to the
# actual patient-record endpoints rather than every GET in the API (which
# would double the audit table's write volume for enquiry/telephony/
# analytics traffic that isn't a patient-record read at all).
PATIENT_RECORD_READ_PREFIXES = (
    "/api/v1/patients/",
    "/api/v1/documents/",
    "/api/v1/prescriptions/",
)


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

        if request.path.startswith("/admin/"):
            return response

        is_mutation = request.method in MUTATING_METHODS
        is_patient_record_read = request.method in ("GET", "HEAD") and request.path.startswith(PATIENT_RECORD_READ_PREFIXES)

        if is_mutation or is_patient_record_read:
            user = getattr(request, "user", None)
            AuditLog.objects.create(
                hospital_id=getattr(user, "hospital_id", None) if user else None,
                actor=user if user and getattr(user, "is_authenticated", False) else None,
                action=AuditLog.Action.REQUEST if is_mutation else AuditLog.Action.READ,
                method=request.method,
                path=request.path,
                status_code=response.status_code,
                ip_address=get_client_ip(request),
            )
        return response


class PayloadEncryptionMiddleware:
    """
    Application-layer payload encryption (Layer 2).

    When PAYLOAD_ENCRYPTION_ENABLED=True AND the request carries an
    X-Session-Id header (set by the frontend after ECDH handshake):

    - Inbound:  Decrypts {"enc":"gcm2$..."} body back to plain JSON before
                the view ever sees it, so all existing DRF view/serializer
                code is completely unaware of the encryption.

    - Outbound: Re-encrypts the JSON response body to {"enc":"gcm2$..."}
                before it leaves the server.

    When PAYLOAD_ENCRYPTION_ENABLED=False (default / dev / Postman):
        Middleware is a transparent no-op -- all traffic passes through
        as plain JSON exactly as before.

    Must be placed FIRST in MIDDLEWARE (before SecurityMiddleware) so it
    rewrites request.body before any other middleware reads it.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.enabled = getattr(settings, "PAYLOAD_ENCRYPTION_ENABLED", False)

    def __call__(self, request):
        # Fast-path: feature flag off or bypass path.
        if not self.enabled or self._is_bypass(request):
            return self.get_response(request)

        session_id = request.headers.get("X-Session-Id", "")
        if not session_id:
            # No session header -- client hasn't done ECDH yet or is Postman.
            # Pass through without encryption to avoid breaking health checks
            # and third-party integrations that don't know about this layer.
            return self.get_response(request)

        aes_key = self._get_key(session_id)
        if aes_key is None:
            return JsonResponse(
                {"detail": "Encryption session not found or expired. Re-initialise via POST /api/v1/session-key/."},
                status=401,
            )

        # ── Decrypt request body ──────────────────────────────────────────────
        if request.method in ("POST", "PUT", "PATCH") and request.body:
            try:
                wrapper = json.loads(request.body)
                enc_token = wrapper.get("enc", "")
                if enc_token:
                    plaintext = decrypt_payload(enc_token, aes_key)
                    # Overwrite the cached body Django uses for parsing.
                    request._body = plaintext
                    request.content_type = "application/json"
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("PayloadEncryptionMiddleware: failed to decrypt request: %s", exc)
                return JsonResponse({"detail": "Encrypted payload could not be decrypted."}, status=400)

        response = self.get_response(request)

        # ── Encrypt response body ─────────────────────────────────────────────
        content_type = response.get("Content-Type", "")
        if response.status_code != 204 and "application/json" in content_type:
            try:
                token = encrypt_payload(response.content, aes_key)
                encrypted_body = json.dumps({"enc": token}).encode("utf-8")
                response.content = encrypted_body
                response["Content-Length"] = len(encrypted_body)
            except Exception as exc:
                # Log but don't crash -- returning unencrypted is safer than 500.
                logger.error("PayloadEncryptionMiddleware: failed to encrypt response: %s", exc)

        return response

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _is_bypass(self, request) -> bool:
        return any(request.path.startswith(p) for p in _ENCRYPTION_BYPASS_PATHS)

    def _get_key(self, session_id: str):
        hex_key = cache.get(f"{_SESSION_CACHE_PREFIX}{session_id}")
        if not hex_key:
            return None
        return bytes.fromhex(hex_key)
