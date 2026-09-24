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
from .models import SupportTicket, TenantInvoice, TenantSubscription, TenantUsageSnapshot
from .pdf import render_invoice_pdf
from .serializers import (
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
        if self.action in {"create", "update", "partial_update", "destroy", "update_modules", "toggle_status"}:
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

    @action(detail=True, methods=["post"], url_path="toggle-status")
    def toggle_status(self, request, pk=None):
        if not request.user.has_saas_capability("tenant_manage"):
            return Response({"detail": "You do not have permission to change hospital status."}, status=status.HTTP_403_FORBIDDEN)
        hospital = self.get_object()
        hospital.is_active = not hospital.is_active
        hospital.save(update_fields=["is_active"])
        return Response(SaaSHospitalSerializer(hospital).data)
