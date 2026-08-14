from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .models import AuditLog, Hospital
from .serializers import AuditLogSerializer, HospitalSerializer


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


class HospitalViewSet(viewsets.ModelViewSet):
    """SaaS Management ViewSet for Hospital tenants & enabled module subscriptions.
    Staff/Superusers can update enabled_modules for any hospital."""

    serializer_class = HospitalSerializer
    permission_classes = [IsAdminUser]
    queryset = Hospital.objects.all()

    def perform_create(self, serializer):
        hospital = serializer.save()

        # Auto-provision default Hospital Owner / Admin role & admin user account
        from apps.accounts.models import Role, User
        from apps.accounts.permission_templates import apply_permission_template

        role, _ = Role.objects.get_or_create(
            hospital=hospital,
            name="Hospital Owner / Admin",
            defaults={"template": Role.Template.HOSPITAL_ADMINISTRATOR},
        )
        role.template = Role.Template.HOSPITAL_ADMINISTRATOR
        role.save()
        apply_permission_template(role.group, Role.Template.HOSPITAL_ADMINISTRATOR)

        admin_email = f"admin@{hospital.slug}.example"
        admin_user, _ = User.objects.get_or_create(
            email=admin_email,
            defaults={
                "first_name": "Hospital",
                "last_name": "Admin",
                "hospital": hospital,
                "role": role,
                "is_staff": True,
            },
        )
        admin_user.set_password("changeme123")
        admin_user.groups.add(role.group)
        admin_user.save()

    @action(detail=True, methods=["post", "patch"], url_path="update-modules")
    def update_modules(self, request, pk=None):
        hospital = self.get_object()
        modules = request.data.get("enabled_modules")
        if not isinstance(modules, list):
            return Response({"error": "enabled_modules must be a list of module keys"}, status=status.HTTP_400_BAD_REQUEST)
        hospital.enabled_modules = modules
        hospital.save(update_fields=["enabled_modules"])
        return Response(HospitalSerializer(hospital).data)

    @action(detail=True, methods=["post", "patch"], url_path="toggle-status")
    def toggle_status(self, request, pk=None):
        hospital = self.get_object()
        hospital.is_active = not hospital.is_active
        hospital.save(update_fields=["is_active"])
        return Response(HospitalSerializer(hospital).data)
