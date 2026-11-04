import hashlib
import io
import json
import logging
import re

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse

from .models import AuditLog, RESERVED_HOSPITAL_SLUGS
from .payload_crypto import decrypt_payload, encrypt_payload
from .request_utils import get_client_ip
from .tenancy import reset_current_hospital_id, set_current_hospital_id

logger = logging.getLogger(__name__)

_SESSION_CACHE_PREFIX = "payload_enc_session:"
_SEEN_CACHE_PREFIX = "payload_enc_seen:"
_SESSION_TTL_SECONDS = 60 * 60 * 12  # as the session itself (apps.core.views)

# With PAYLOAD_ENCRYPTION_ENABLED, every /api/ call must be encrypted except
# these, which can't do the handshake. Each is a machine, a browser redirect or
# a page on someone else's website, never the staff or patient app. Anything
# new is encrypted by default: it only gets here by being added on purpose.
_PLAINTEXT_ALLOWED = re.compile(r"^/api/(" + "|".join([
    r"health/",
    r"schema/",  # staff-only in production, no patient data
    r"license/status/",  # operators' curl (RUNBOOK), no patient data
    r"v1/session-key/",  # the handshake itself
    # Servers and devices, authenticated by their own signature, token or secret:
    r"v1/webhooks/",
    r"v1/ivr/",
    r"v1/leads/",
    r"v1/enquiries/lead-webhook/",
    r"v1/billing/payment-webhook/",
    r"v1/laboratory/analyzer/",
    r"v1/clinical/device-ingest/",
    r"v1/support-services/ambulance-device/",
    r"v1/abdm/callbacks/",  # gateway JWT
    r"v1/abdm/data-push/",  # content is end-to-end encrypted (apps.abdm.fidelius)
    # Plain browser navigation: no script runs, so no handshake is possible.
    r"v1/auth/sso/\d+/start/",
    r"v1/auth/sso/callback/",
    r"v1/calendar/callback/",
    r"v1/unsubscribe/",
    # Links in patient messages: without a session these only redirect to the
    # app (see shared_link_redirect), which fetches the file encrypted.
    r"v1/laboratory/report-link/",
    r"v1/finance/payout-statement/",
    # The widget embedded on the hospital's own website, another origin.
    r"v1/site/",
]) + r")")

_SESSION_REQUIRED = "Encryption session not found or expired. Re-initialise via POST /api/v1/session-key/."


MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Part A #6 — "every read of a patient record is logged". Scoped to the
# actual patient-record endpoints rather than every GET in the API (which
# would double the audit table's write volume for enquiry/telephony/
# analytics traffic that isn't a patient-record read at all).
PATIENT_RECORD_READ_PREFIXES = (
    "/api/v1/patients/",
    "/api/v1/documents/",
    "/api/v1/prescriptions/",
    # the clinical / financial record itself, wherever it is read from
    "/api/v1/opd/",
    "/api/v1/ipd/",
    "/api/v1/laboratory/",
    "/api/v1/radiology/",
    "/api/v1/nursing/",
    "/api/v1/emergency/",
    "/api/v1/ot/",
    "/api/v1/icu/",
    "/api/v1/clinical/",
    "/api/v1/pharmacy/",
    "/api/v1/billing/",
    "/api/v1/tpa/",
    "/api/v1/mrd/",
    "/api/v1/oncology/",
    "/api/v1/cathlab/",
    "/api/v1/telemedicine/",
    "/api/v1/dietary/",
    "/api/v1/infection-control/",
    "/api/v1/abdm/received-records/",
    "/api/v1/bloodbank/",
    "/api/v1/schemes/",
    "/api/v1/abdm/",
)


def get_subdomain_from_request(request):
    try:
        host = request.get_host().split(":")[0].lower()
        if host.replace(".", "").isdigit():  # an IP address (192.168.1.156) is never a hospital subdomain
            return None
        parts = host.split(".")
        if len(parts) >= 3 and parts[0] not in ("www", "api"):
            return parts[0]
        if len(parts) == 2 and parts[1] == "localhost":
            return parts[0]
    except Exception:
        pass
    return None


class TenantMiddleware:
    """Resolves the current hospital from the authenticated user and makes
    it available to TenantManager for the duration of the request. Users
    who can act across tenants (apps.accounts.models.User.can_cross_tenant
    — platform ops, not ordinary hospital staff) may switch tenant via the
    X-Hospital-Id header (used by internal ops tooling / superadmin
    dashboards that operate across hospitals). Also supports
    subdomain-based tenant resolution (e.g. demo-hospital.hms.polynexus.in)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        hospital_id = None
        user = getattr(request, "user", None)

        subdomain = request.headers.get("X-Tenant") or get_subdomain_from_request(request)
        tenant_from_subdomain = None
        if subdomain and subdomain not in RESERVED_HOSPITAL_SLUGS:
            from .models import Hospital
            tenant_from_subdomain = Hospital.objects.filter(slug=subdomain, is_active=True).first()

        if tenant_from_subdomain is None:
            from apps.licensing.service import is_on_premise

            if is_on_premise():  # a single-hospital installation: no subdomains, one tenant
                from .models import Hospital
                tenant_from_subdomain = Hospital.objects.filter(is_active=True).order_by("created_at").first()

        request.subdomain = subdomain
        request.tenant = tenant_from_subdomain

        if user is not None and getattr(user, "is_authenticated", False):
            if user.can_cross_tenant and request.headers.get("X-Hospital-Id"):
                hospital_id = request.headers["X-Hospital-Id"]
            elif getattr(user, "hospital_id", None):
                hospital_id = user.hospital_id
            elif tenant_from_subdomain:
                hospital_id = tenant_from_subdomain.id
        elif tenant_from_subdomain:
            hospital_id = tenant_from_subdomain.id

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
            from django.contrib.auth.base_user import AbstractBaseUser

            is_staff_user = isinstance(user, AbstractBaseUser) and user.is_authenticated
            AuditLog.objects.create(
                hospital_id=getattr(user, "hospital_id", None) if user else None,
                # Patient-portal requests authenticate as a PortalPrincipal,
                # not a User row — hospital is still recorded, actor isn't.
                actor=user if is_staff_user else None,
                action=AuditLog.Action.REQUEST if is_mutation else AuditLog.Action.READ,
                method=request.method,
                path=request.path,
                status_code=response.status_code,
                ip_address=get_client_ip(request),
            )
        return response


class PayloadEncryptionMiddleware:
    """
    Application-layer payload encryption (Layer 2), on top of HTTPS.

    With PAYLOAD_ENCRYPTION_ENABLED (always, in production) this is a hard
    requirement, not an opt-in. For every /api/ call outside _PLAINTEXT_ALLOWED:

    - A session from the ECDH handshake (X-Session-Id) is required: without
      one the call is refused, before any view runs.
    - The body must be {"enc": "gcm2$..."}: JSON, or the raw bytes of a
      multipart upload with its original type in "ct". A plain body is refused.
    - The query string must travel in the X-Enc-Query header. A plain one
      (search terms, patient ids) is refused.
    - A token can only be used once, so a captured request can't be replayed.
    - Every response body is encrypted: JSON as {"enc": ...}; anything else
      (PDF, image, CSV, an error page) as {"enc": ..., "ct": original type},
      with Content-Disposition removed. If encryption fails the client gets a
      500, never the plain body.

    An allowed path that does carry a session gets all of the above too.
    Must be FIRST in MIDDLEWARE so nothing reads the body before it is opened.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            not getattr(settings, "PAYLOAD_ENCRYPTION_ENABLED", False)
            or not request.path.startswith("/api/")
            or request.method == "OPTIONS"  # CORS preflight: carries nothing
            or request.path == "/api/v1/session-key/"
        ):
            return self.get_response(request)

        session_id = request.headers.get("X-Session-Id", "")
        if not session_id:
            if _PLAINTEXT_ALLOWED.match(request.path):
                return self.get_response(request)
            return JsonResponse({"detail": _SESSION_REQUIRED}, status=401)

        aes_key = self._get_key(session_id)
        if aes_key is None:
            return JsonResponse({"detail": _SESSION_REQUIRED}, status=401)

        try:
            self._open_query(request, session_id, aes_key)
            self._open_body(request, session_id, aes_key)
        except _Refused as exc:
            logger.warning("PayloadEncryptionMiddleware refused %s %s: %s", request.method, request.path, exc)
            return JsonResponse({"detail": str(exc)}, status=exc.status)

        return self._seal(self.get_response(request), aes_key)

    # ── Inbound ──────────────────────────────────────────────────────────────

    def _open(self, token, session_id, aes_key):
        try:
            plaintext = decrypt_payload(token, aes_key)
        except ValueError as exc:
            raise _Refused("Encrypted payload could not be decrypted.") from exc
        seen = hashlib.sha256(token.encode("ascii", "replace")).hexdigest()
        if not cache.add(f"{_SEEN_CACHE_PREFIX}{session_id}:{seen}", 1, timeout=_SESSION_TTL_SECONDS):
            raise _Refused("This encrypted request was already used.")
        return plaintext

    def _open_query(self, request, session_id, aes_key):
        token = request.headers.get("X-Enc-Query", "")
        if request.META.get("QUERY_STRING"):
            raise _Refused("Send the query string encrypted, in X-Enc-Query.")
        if token:
            request.META["QUERY_STRING"] = self._open(token, session_id, aes_key).decode("utf-8")
            request.__dict__.pop("GET", None)  # cached_property: re-read from META

    def _open_body(self, request, session_id, aes_key):
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        if length > settings.PAYLOAD_MAX_BODY_BYTES:
            raise _Refused("Request is too large.", status=413)
        # .read(), not .body: an encrypted upload is legitimately larger than
        # DATA_UPLOAD_MAX_MEMORY_SIZE, and .body refuses that.
        raw = request.read() if length else b""
        if not raw:
            return
        try:
            wrapper = json.loads(raw)
            token = wrapper["enc"]
            content_type = str(wrapper.get("ct") or "application/json")
        except (ValueError, KeyError, TypeError) as exc:
            raise _Refused("The request body must be encrypted.") from exc
        if not content_type.startswith(("application/json", "multipart/form-data")):
            raise _Refused("Unsupported encrypted content type.")
        plaintext = self._open(token, session_id, aes_key)
        # Replace both the cached body and the stream: DRF parses from the stream.
        request._body = plaintext
        request._stream = io.BytesIO(plaintext)
        request._read_started = False
        request.META["CONTENT_LENGTH"] = str(len(plaintext))
        request.META["CONTENT_TYPE"] = content_type
        request._set_content_type_params(request.META)

    # ── Outbound ─────────────────────────────────────────────────────────────

    def _seal(self, response, aes_key):
        if response.status_code == 204 or 300 <= response.status_code < 400:
            return response  # no body
        try:
            if response.streaming:
                content = b"".join(response.streaming_content)
                response.close()
            else:
                content = response.content
            if not content:
                return response
            content_type = response.get("Content-Type", "")
            wrapper = {"enc": encrypt_payload(content, aes_key)}
            if "application/json" not in content_type:
                wrapper["ct"] = content_type
            sealed = HttpResponse(json.dumps(wrapper).encode("utf-8"), status=response.status_code, content_type="application/json")
            for name, value in response.items():
                if name.lower() not in ("content-type", "content-length", "content-disposition"):
                    sealed[name] = value
            sealed.cookies = response.cookies
            return sealed
        except Exception:
            # Never fall back to the plain body.
            logger.exception("PayloadEncryptionMiddleware: failed to encrypt a response")
            return JsonResponse({"detail": "The response could not be encrypted."}, status=500)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_key(self, session_id: str):
        hex_key = cache.get(f"{_SESSION_CACHE_PREFIX}{session_id}")
        if not hex_key:
            return None
        return bytes.fromhex(hex_key)


class _Refused(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def shared_link_redirect(request, app_path):
    """A signed link from a patient message, opened in a browser: there is no
    session to encrypt the file for, so send the browser to the app page that
    has one instead of returning the document. None when no redirect applies."""
    if getattr(settings, "PAYLOAD_ENCRYPTION_ENABLED", False) and not request.headers.get("X-Session-Id"):
        return HttpResponseRedirect(f"{settings.PUBLIC_APP_URL.rstrip('/')}{app_path}")
    return None


class APIContentSecurityPolicyMiddleware:
    """A JSON API response is data, never a page: a strict CSP on it means
    that if a browser is ever tricked into rendering one (a reflected value,
    a sniffed content type), nothing in it can run or be framed. The SPA's
    own policy is set by nginx (Frontend nginx/security-headers.inc); the
    Django admin and Swagger UI pages are HTML and keep their own."""

    POLICY = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if "application/json" in response.get("Content-Type", "") and "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = self.POLICY
        return response
