import uuid

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AuditLog, EmergencyAccessLog
from .payload_crypto import derive_shared_aes_key, get_server_public_key_b64
from .permissions import CanReviewEmergencyAccess
from .serializers import AuditLogSerializer, EmergencyAccessLogSerializer

# Session TTL matches the refresh token lifetime (12 h by default).
_SESSION_TTL_SECONDS = 60 * 60 * 12
_SESSION_CACHE_PREFIX = "payload_enc_session:"


class SessionKeyView(APIView):
    """
    POST /api/v1/session-key/

    ECDH key-exchange handshake. The client sends its ephemeral P-256 public
    key; the server derives the shared AES-256 key via ECDH + HKDF, caches it
    under a random session_id, and returns its own public key so the client can
    derive the same shared secret independently.

    No authentication required -- the handshake happens before login.
    The AuditMiddleware / TenantMiddleware are intentionally bypassed for this
    endpoint (no user context yet).

    Request body:
        { "client_public_key": "<urlsafe-base64 uncompressed P-256 point>" }

    Response:
        {
            "session_id": "<uuid>",
            "server_public_key": "<urlsafe-base64 uncompressed P-256 point>"
        }
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []  # rate-limit via nginx/WAF upstream

    def get(self, request):
        """Provides a session key for frontend clients requesting /api/v1/session-key/."""
        if not request.session.session_key:
            request.session.create()
        key = request.session.session_key
        return Response(
            {
                "session_key": key,
                "sessionKey": key,
                "status": "success",
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        if not getattr(settings, "PAYLOAD_ENCRYPTION_ENABLED", False):
            return Response(
                {"detail": "Payload encryption is not enabled on this server."},
                status=status.HTTP_404_NOT_FOUND,
            )

        client_public_key = request.data.get("client_public_key", "")
        if not client_public_key:
            return Response(
                {"detail": "client_public_key is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            aes_key = derive_shared_aes_key(client_public_key)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        session_id = str(uuid.uuid4())
        # Store as hex string — bytes are not JSON-serializable in all cache backends.
        cache.set(
            f"{_SESSION_CACHE_PREFIX}{session_id}",
            aes_key.hex(),
            timeout=_SESSION_TTL_SECONDS,
        )

        return Response(
            {
                "session_id": session_id,
                "server_public_key": get_server_public_key_b64(),
            },
            status=status.HTTP_200_OK,
        )


class EmergencyAccessLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Review surface for break-glass access (Part A #6). Read-only by
    design — the only write path is mark_reviewed below, which stamps
    reviewed/reviewed_by/reviewed_at together rather than allowing a bare
    PATCH to set `reviewed=True` with no reviewer attached."""

    serializer_class = EmergencyAccessLogSerializer
    permission_classes = [CanReviewEmergencyAccess]
    queryset = EmergencyAccessLog.objects.none()  # schema-generation fallback; get_queryset() below does the real filtering
    filterset_fields = ["reviewed", "model_name", "actor"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False) or not self.request.user.is_authenticated:
            return EmergencyAccessLog.objects.none()
        user = self.request.user
        if user.can_cross_tenant and self.request.headers.get("X-Hospital-Id"):
            hospital_id = self.request.headers["X-Hospital-Id"]
        else:
            hospital_id = user.hospital_id
        return EmergencyAccessLog.objects.filter(hospital_id=hospital_id)

    @action(detail=True, methods=["post"])
    def mark_reviewed(self, request, pk=None):
        log = self.get_object()
        log.reviewed = True
        log.reviewed_by = request.user
        log.reviewed_at = timezone.now()
        log.review_notes = str(request.data.get("review_notes", ""))
        log.save(update_fields=["reviewed", "reviewed_by", "reviewed_at", "review_notes"])
        return Response(EmergencyAccessLogSerializer(log).data, status=status.HTTP_200_OK)


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only view onto the immutable AuditLog trail (models.py). Admin
    Console only — gated with IsAdminUser (is_staff), matching how
    UserViewSet/RoleViewSet already elevate staff users elsewhere.
    AuditLog isn't a TenantScopedModel (hospital is nullable, since some
    entries — failed logins, etc. — may predate tenant resolution), so it's
    scoped by hand rather than via TenantManager."""

    serializer_class = AuditLogSerializer
    permission_classes = [IsAdminUser]
    queryset = AuditLog.objects.none()  # schema-generation fallback; get_queryset() below does the real filtering
    filterset_fields = ["action", "model_name", "actor"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False) or not self.request.user.is_authenticated:
            return AuditLog.objects.none()
        return AuditLog.objects.filter(hospital_id=self.request.user.hospital_id)


session_key_view = SessionKeyView.as_view()
