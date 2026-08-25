import secrets

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .models import ALL_MODULE_KEYS, AuditLog, Hospital
from .serializers import AuditLogSerializer, HospitalSerializer


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only view onto the immutable AuditLog trail (models.py). Admin
    Console only — gated with IsAdminUser (is_staff), matching how
    UserViewSet/RoleViewSet already elevate staff users elsewhere.
    AuditLog isn't a TenantScopedModel (hospital is nullable, since some
    entries — failed logins, etc. — may predate tenant resolution), so it's
    scoped by hand rather than via TenantManager.

    Staff/SaaS-admins get the platform-wide trail (optionally narrowed to
    one hospital via ?hospital=<id>) — the same X-Hospital-Id-style
    broadening every other staff-aware view in this codebase gives, and
    the reason AuditLogSerializer already carries PII-redaction logic for
    a cross-tenant viewer (get_object_repr/get_changes) that had no real
    caller before this. Everyone else stays hard-scoped to their own
    hospital, unchanged."""

    serializer_class = AuditLogSerializer
    permission_classes = [IsAdminUser]
    queryset = AuditLog.objects.none()  # schema-generation fallback; get_queryset() below does the real filtering
    filterset_fields = ["action", "model_name", "actor"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False) or not self.request.user.is_authenticated:
            return AuditLog.objects.none()
        user = self.request.user
        if user.is_staff:
            hospital_id = self.request.query_params.get("hospital")
            queryset = AuditLog.objects.all()
            return queryset.filter(hospital_id=hospital_id) if hospital_id else queryset
        return AuditLog.objects.filter(hospital_id=user.hospital_id)


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

        # `is_staff` in this codebase means "platform-ops account" — it's
        # what TenantScopedViewSetMixin/UserViewSet.switch_hospital/
        # HospitalViewSet/AuditLogViewSet all treat as a cross-hospital
        # override via X-Hospital-Id (see apps.core.viewsets docstring).
        # This used to set is_staff=True on every tenant's own auto-created
        # admin, which — combined with Django admin's ModelAdmin classes
        # having no per-hospital scoping at all — meant every hospital's
        # "Hospital Owner / Admin" could pass X-Hospital-Id and read/write
        # every *other* hospital's data, or browse every other hospital's
        # Users/Patients/etc. through /admin/. This account's permissions
        # come entirely from its Role/Group (apply_permission_template
        # above) like every other hospital user — it doesn't need, and
        # must not get, the platform-staff flag.
        requested_email = (self.request.data.get("admin_email") or "").strip().lower()
        admin_email = requested_email or f"admin@{hospital.slug}.example"
        generated_password = secrets.token_urlsafe(18)
        admin_user, created = User.objects.get_or_create(
            email=admin_email,
            defaults={
                "first_name": "Hospital",
                "last_name": "Admin",
                "hospital": hospital,
                "role": role,
            },
        )
        if created:
            admin_user.set_password(generated_password)
            admin_user.groups.add(role.group)
            admin_user.save()
            self._provisioned_admin_credentials = {"email": admin_email, "password": generated_password}

    def create(self, request, *args, **kwargs):
        self._provisioned_admin_credentials = None
        response = super().create(request, *args, **kwargs)
        if self._provisioned_admin_credentials:
            # Returned once, here, only — never logged or persisted in
            # plaintext (the password itself is only ever passed through
            # set_password() above).
            response.data["provisioned_admin"] = self._provisioned_admin_credentials
        return response

    @action(detail=True, methods=["post", "patch"], url_path="update-modules")
    def update_modules(self, request, pk=None):
        hospital = self.get_object()
        modules = request.data.get("enabled_modules")
        if not isinstance(modules, list) or not all(isinstance(key, str) for key in modules):
            return Response({"error": "enabled_modules must be a list of module keys"}, status=status.HTTP_400_BAD_REQUEST)
        unknown = sorted(set(modules) - set(ALL_MODULE_KEYS))
        if unknown:
            return Response({"error": f"Unknown module key(s): {', '.join(unknown)}"}, status=status.HTTP_400_BAD_REQUEST)
        hospital.enabled_modules = modules
        hospital.save(update_fields=["enabled_modules"])
        return Response(HospitalSerializer(hospital).data)

    @action(detail=True, methods=["post", "patch"], url_path="toggle-status")
    def toggle_status(self, request, pk=None):
        hospital = self.get_object()
        hospital.is_active = not hospital.is_active
        hospital.save(update_fields=["is_active"])
        return Response(HospitalSerializer(hospital).data)
