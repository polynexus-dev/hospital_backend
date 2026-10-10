from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import ALL_MODULES, Hospital
from apps.core.permissions import CanManageSaaSBilling, CanManageSaaSSupport, CanManageSaaSTenants, CanViewSaaSAnalytics, CanViewSaaSTenants, IsSaaSAdmin
from apps.core.viewsets import TenantScopedViewSetMixin

from . import services
from .models import OnPremiseLicense, SupportTicket, TenantInvoice, TenantSubscription, TenantUsageSnapshot
from .pdf import render_invoice_pdf
from .serializers import (
    GenerateLicenseSerializer,
    OnPremiseLicenseSerializer,
    SaaSHospitalSerializer,
    SaaSSupportTicketSerializer,
    SupportTicketSerializer,
    TenantInvoiceSerializer,
    TenantSubscriptionSerializer,
    TenantUsageSnapshotSerializer,
)
from .tenant_service import onboard_hospital_tenant



class TenantSubscriptionViewSet(viewsets.ModelViewSet):
    """SaaS-admin only — plain `Model.objects.all()` is correct here (no
    TenantScopedViewSetMixin): a SaaS admin manages every tenant's
    subscription, that's the entire point of this surface."""

    serializer_class = TenantSubscriptionSerializer
    permission_classes = [IsAuthenticated, CanManageSaaSBilling]
    # select_related: TenantSubscriptionSerializer.hospital_name (source="hospital.name")
    queryset = TenantSubscription.objects.select_related("hospital")
    filterset_fields = ["hospital", "tier", "status"]
    search_fields = ["hospital__name", "hospital__slug", "hospital__city"]


class TenantInvoiceViewSet(viewsets.ModelViewSet):
    serializer_class = TenantInvoiceSerializer
    permission_classes = [IsAuthenticated, CanManageSaaSBilling]
    # select_related: TenantInvoiceSerializer.hospital_name (source="hospital.name")
    queryset = TenantInvoice.objects.select_related("hospital")
    filterset_fields = ["hospital", "status"]
    search_fields = ["invoice_number", "hospital__name", "hospital__slug"]

    def perform_create(self, serializer):
        serializer.save(invoice_number=services.generate_invoice_number())

    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        invoice = self.get_object()
        invoice.status = TenantInvoice.Status.PAID
        invoice.paid_at = timezone.now()
        invoice.save(update_fields=["status", "paid_at"])
        return Response(TenantInvoiceSerializer(invoice).data)

    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        invoice = self.get_object()
        pdf_bytes = render_invoice_pdf(invoice)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{invoice.invoice_number}.pdf"'
        return response


class TenantUsageSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only — rows are written exclusively by
    apps.saas_admin.tasks.compute_monthly_tenant_usage."""

    serializer_class = TenantUsageSnapshotSerializer
    permission_classes = [IsAuthenticated, CanViewSaaSAnalytics]
    # select_related: TenantUsageSnapshotSerializer.hospital_name (source="hospital.name")
    queryset = TenantUsageSnapshot.objects.select_related("hospital")
    filterset_fields = ["hospital", "period_start"]
    search_fields = ["hospital__name", "hospital__slug"]


class SaaSSupportTicketViewSet(viewsets.ModelViewSet):
    """SaaS-admin side — every hospital's tickets, full triage
    capability. Counterpart to SupportTicketViewSet below (hospital-side,
    create/view only)."""

    serializer_class = SaaSSupportTicketSerializer
    permission_classes = [IsAuthenticated, CanManageSaaSSupport]
    # select_related: SaaSSupportTicketSerializer's hospital_name/raised_by_email/
    # assigned_to_email (source="hospital.name"/"raised_by.email"/"assigned_to.email")
    queryset = SupportTicket.objects.select_related("hospital", "raised_by", "assigned_to")
    filterset_fields = ["hospital", "status", "priority", "category", "assigned_to"]
    search_fields = ["subject", "hospital__name", "hospital__slug", "raised_by_email"]

    @action(detail=True, methods=["post"], url_path="resolve")
    def resolve(self, request, pk=None):
        ticket = self.get_object()
        ticket.status = SupportTicket.Status.RESOLVED
        ticket.resolution_notes = request.data.get("resolution_notes", ticket.resolution_notes)
        ticket.resolved_at = timezone.now()
        ticket.save(update_fields=["status", "resolution_notes", "resolved_at"])
        return Response(SaaSSupportTicketSerializer(ticket).data)

    @action(detail=True, methods=["post"], url_path="assign")
    def assign(self, request, pk=None):
        assigned_to_id = request.data.get("assigned_to")
        if not assigned_to_id:
            return Response({"error": "assigned_to is required."}, status=status.HTTP_400_BAD_REQUEST)
        from apps.accounts.models import User

        try:
            assignee = User.objects.get(pk=assigned_to_id, is_saas_admin=True)
        except User.DoesNotExist:
            return Response({"error": "assigned_to must be a SaaS admin user."}, status=status.HTTP_400_BAD_REQUEST)
        ticket = self.get_object()
        ticket.assigned_to = assignee
        ticket.status = SupportTicket.Status.IN_PROGRESS if ticket.status == SupportTicket.Status.OPEN else ticket.status
        ticket.save(update_fields=["assigned_to", "status"])
        return Response(SaaSSupportTicketSerializer(ticket).data)


class SupportTicketViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """Hospital-side — any authenticated hospital user can raise/view
    their own hospital's tickets. Deliberately `[IsAuthenticated]` only
    (not the project-wide default permission stack — see
    REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"]):

    - Skips RoleBasedModelPermissions: raising a support ticket is a
      baseline capability every hospital role should have, not a
      privileged CRUD action gated by a Django `saas_admin.add_
      supportticket` permission that no Role template grants (this app
      is deliberately absent from apps.accounts.permission_templates —
      support-ticket creation shouldn't require a platform-admin to
      remember to add it to every template).
    - Skips HospitalActive: a suspended hospital's users must still be
      able to open a ticket (e.g. to ask about, or contest, the
      suspension) — the one place a suspended tenant should still reach
      the platform.

    No status/assign/resolve here — see SupportTicketSerializer and
    SaaSSupportTicketViewSet above."""

    serializer_class = SupportTicketSerializer
    permission_classes = [IsAuthenticated]
    # select_related: SupportTicketSerializer.raised_by_email (source="raised_by.email")
    queryset = SupportTicket.objects.select_related("raised_by")
    http_method_names = ["get", "post", "head", "options"]
    filterset_fields = ["status", "category", "priority"]

    def perform_create(self, serializer):
        hospital = self.request.user.hospital
        if hospital is None:
            raise ValidationError("The requesting user is not attached to a hospital.")
        serializer.save(hospital=hospital, raised_by=self.request.user)


class PlatformAnalyticsView(APIView):
    permission_classes = [IsAuthenticated, CanViewSaaSAnalytics]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(services.platform_analytics_snapshot())


class SaaSHospitalViewSet(viewsets.ModelViewSet):
    """
    SaaS Platform management for Hospital Tenants:
    - Lists and searches across all hospitals and branch networks.
    - Full end-to-end tenant onboarding wizard endpoint.
    - Live granular module toggle (OPD, IPD, ICU, OT, Pharmacy, etc.).
    - Activation / suspension switch.
    """

    serializer_class = SaaSHospitalSerializer
    permission_classes = [IsAuthenticated, CanViewSaaSTenants]
    queryset = Hospital.objects.all().select_related("subscription").prefetch_related("users").order_by("-created_at")
    filterset_fields = ["is_active", "city", "state"]
    search_fields = ["name", "slug", "city", "state"]

    def get_permissions(self):
        if self.action in {"create", "update", "partial_update", "destroy", "update_modules", "toggle_status", "generate_license"} or (self.action == "permissions" and self.request.method != "GET"):
            return [IsAuthenticated(), CanManageSaaSTenants()]
        return [IsAuthenticated(), CanViewSaaSTenants()]

    def create(self, request, *args, **kwargs):
        if not request.user.has_saas_capability("tenant_manage"):
            return Response({"detail": "You do not have permission to onboard hospitals."}, status=status.HTTP_403_FORBIDDEN)
        result = onboard_hospital_tenant(request.data)
        hospital = result["hospital"]
        return Response(SaaSHospitalSerializer(hospital).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch", "post"], url_path="modules")
    def update_modules(self, request, pk=None):
        if not request.user.has_saas_capability("tenant_manage"):
            return Response({"detail": "You do not have permission to change hospital modules."}, status=status.HTTP_403_FORBIDDEN)
        hospital = self.get_object()
        raw_modules = request.data.get("enabled_modules")
        if not isinstance(raw_modules, list):
            return Response({"error": "enabled_modules must be a list of module keys."}, status=status.HTTP_400_BAD_REQUEST)

        valid_modules = [m for m in raw_modules if m in ALL_MODULES]
        hospital.enabled_modules = valid_modules
        hospital.save(update_fields=["enabled_modules"])
        return Response(SaaSHospitalSerializer(hospital).data)

    @action(detail=True, methods=["get", "put"])
    def permissions(self, request, pk=None):
        """The hospital's permission ceiling — what its admin may use and
        hand out, within its enabled modules. PUT {"permissions": [...]}
        restricts it; {"permissions": null} lifts the restriction."""
        from apps.accounts.permission_catalog import catalog, catalog_codes, hospital_bounds
        from apps.governance import services as gov
        from apps.governance.models import SecurityEvent

        hospital = self.get_object()
        in_modules = hospital_bounds(hospital, ignore_ceiling=True)[0]
        if request.method == "PUT":
            submitted = request.data.get("permissions")
            if submitted is not None:
                if not isinstance(submitted, list) or not all(isinstance(c, str) for c in submitted):
                    return Response({"permissions": ["Send a list of \"app.codename\" strings, or null for no restriction."]}, status=status.HTTP_400_BAD_REQUEST)
                unknown = set(submitted) - catalog_codes()
                if unknown:
                    return Response({"permissions": [f"Unknown permission: {c}" for c in sorted(unknown)[:10]]}, status=status.HTTP_400_BAD_REQUEST)
                # Keep codes from currently disabled modules: switching a
                # module back on shouldn't silently widen or narrow access.
                kept = set(hospital.permission_ceiling or []) - in_modules if hospital.permission_ceiling is not None else set()
                submitted = sorted((set(submitted) & in_modules) | kept)
            hospital.permission_ceiling = submitted
            hospital.save(update_fields=["permission_ceiling"])
            gov.log_security_event(
                SecurityEvent.EventType.PERMISSIONS_CHANGED, request=request, user=request.user, hospital_id=hospital.pk,
                details={"target": "hospital_ceiling", "restricted": submitted is not None, "count": len(submitted or [])},
            )
        allowed = hospital_bounds(hospital)[0]
        return Response({
            "catalog": catalog(in_modules),
            "restricted": hospital.permission_ceiling is not None,
            "selected": sorted(allowed),
        })

    @action(detail=True, methods=["post"], url_path="generate-license")
    def generate_license(self, request, pk=None):
        """Sign an on-premise license for this hospital. Returns the .lic
        file, or the license record with {"response": "json"}."""
        from apps.governance import services as gov
        from apps.governance.models import SecurityEvent
        from apps.licensing.issuing import SigningKeyMissing, issue_license

        hospital = self.get_object()
        params = GenerateLicenseSerializer(data=request.data)
        params.is_valid(raise_exception=True)
        d = params.validated_data
        try:
            record = issue_license(
                hospital, issued_by=request.user, duration_days=d["duration_days"], enabled_modules=d["modules"],
                machine_fingerprint=d["machine_fingerprint"], max_active_users=d["max_users"], max_beds=d["max_beds"],
                grace_period_days=d["grace_period_days"], tier=d["tier"],
            )
        except SigningKeyMissing as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        gov.log_security_event(
            SecurityEvent.EventType.SAAS_ACCESS_CHANGED, request=request, user=request.user, hospital_id=hospital.pk,
            details={"action": "license_issued", "license_id": record.license_id, "expires_at": record.expires_at.isoformat()},
        )
        if request.data.get("response") == "json":
            return Response(OnPremiseLicenseSerializer(record).data, status=status.HTTP_201_CREATED)
        return license_file_response(record)

    @action(detail=True, methods=["post"], url_path="toggle-status")
    def toggle_status(self, request, pk=None):
        if not request.user.has_saas_capability("tenant_manage"):
            return Response({"detail": "You do not have permission to change hospital status."}, status=status.HTTP_403_FORBIDDEN)
        hospital = self.get_object()
        hospital.is_active = not hospital.is_active
        hospital.save(update_fields=["is_active"])
        return Response(SaaSHospitalSerializer(hospital).data)


def license_file_response(record):
    response = HttpResponse(record.license_file + "\n", content_type="application/octet-stream")
    response["Content-Disposition"] = f'attachment; filename="{record.license_id}.lic"'
    return response


class OnPremiseLicenseViewSet(viewsets.ReadOnlyModelViewSet):
    """Issued on-premise licenses: history, re-download, revoke."""

    serializer_class = OnPremiseLicenseSerializer
    permission_classes = [IsAuthenticated, CanViewSaaSTenants]
    queryset = OnPremiseLicense.objects.select_related("hospital", "issued_by")
    filterset_fields = ["hospital"]

    def get_permissions(self):
        if self.action == "revoke":
            return [IsAuthenticated(), CanManageSaaSTenants()]
        return super().get_permissions()

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        record = self.get_object()
        if record.revoked_at:
            return Response({"detail": "This license has been revoked."}, status=status.HTTP_410_GONE)
        return license_file_response(record)

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        from apps.governance import services as gov
        from apps.governance.models import SecurityEvent

        record = self.get_object()
        if not record.revoked_at:
            record.revoked_at = timezone.now()
            record.revoked_by = request.user
            record.revoke_reason = str(request.data.get("reason", ""))[:255]
            record.save(update_fields=["revoked_at", "revoked_by", "revoke_reason"])
            gov.log_security_event(
                SecurityEvent.EventType.SAAS_ACCESS_CHANGED, request=request, user=request.user, hospital_id=record.hospital_id,
                details={"action": "license_revoked", "license_id": record.license_id, "reason": record.revoke_reason},
            )
        return Response(OnPremiseLicenseSerializer(record).data)
