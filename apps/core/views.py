from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .models import AuditLog, EmergencyAccessLog
from .permissions import CanReviewEmergencyAccess
from .serializers import AuditLogSerializer, EmergencyAccessLogSerializer


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
        if user.is_staff and self.request.headers.get("X-Hospital-Id"):
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
