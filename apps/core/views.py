from rest_framework import viewsets
from rest_framework.permissions import IsAdminUser

from .models import AuditLog
from .serializers import AuditLogSerializer


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
