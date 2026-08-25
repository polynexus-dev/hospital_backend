from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsSaaSAdmin
from apps.core.viewsets import TenantScopedViewSetMixin

from . import services
from .models import SupportTicket, TenantInvoice, TenantSubscription, TenantUsageSnapshot
from .serializers import (
    SaaSSupportTicketSerializer,
    SupportTicketSerializer,
    TenantInvoiceSerializer,
    TenantSubscriptionSerializer,
    TenantUsageSnapshotSerializer,
)


class TenantSubscriptionViewSet(viewsets.ModelViewSet):
    """SaaS-admin only — plain `Model.objects.all()` is correct here (no
    TenantScopedViewSetMixin): a SaaS admin manages every tenant's
    subscription, that's the entire point of this surface."""

    serializer_class = TenantSubscriptionSerializer
    permission_classes = [IsAuthenticated, IsSaaSAdmin]
    queryset = TenantSubscription.objects.all()
    filterset_fields = ["hospital", "tier", "status"]


class TenantInvoiceViewSet(viewsets.ModelViewSet):
    serializer_class = TenantInvoiceSerializer
    permission_classes = [IsAuthenticated, IsSaaSAdmin]
    queryset = TenantInvoice.objects.all()
    filterset_fields = ["hospital", "status"]

    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        invoice = self.get_object()
        invoice.status = TenantInvoice.Status.PAID
        invoice.paid_at = timezone.now()
        invoice.save(update_fields=["status", "paid_at"])
        return Response(TenantInvoiceSerializer(invoice).data)


class TenantUsageSnapshotViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only — rows are written exclusively by
    apps.saas_admin.tasks.compute_monthly_tenant_usage."""

    serializer_class = TenantUsageSnapshotSerializer
    permission_classes = [IsAuthenticated, IsSaaSAdmin]
    queryset = TenantUsageSnapshot.objects.all()
    filterset_fields = ["hospital", "period_start"]


class SaaSSupportTicketViewSet(viewsets.ModelViewSet):
    """SaaS-admin side — every hospital's tickets, full triage
    capability. Counterpart to SupportTicketViewSet below (hospital-side,
    create/view only)."""

    serializer_class = SaaSSupportTicketSerializer
    permission_classes = [IsAuthenticated, IsSaaSAdmin]
    queryset = SupportTicket.objects.all()
    filterset_fields = ["hospital", "status", "priority", "category", "assigned_to"]

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
    queryset = SupportTicket.objects.all()
    http_method_names = ["get", "post", "head", "options"]
    filterset_fields = ["status", "category", "priority"]

    def perform_create(self, serializer):
        hospital = self.request.user.hospital
        if hospital is None:
            raise ValidationError("The requesting user is not attached to a hospital.")
        serializer.save(hospital=hospital, raised_by=self.request.user)


class PlatformAnalyticsView(APIView):
    permission_classes = [IsAuthenticated, IsSaaSAdmin]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(services.platform_analytics_snapshot())
