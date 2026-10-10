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
from .models import LicenseUsageReport, OnPremiseLicense, SupportTicket, TenantInvoice, TenantSubscription, TenantUsageSnapshot
from .pdf import render_invoice_pdf
from .serializers import (
    GenerateLicenseSerializer,
    LicenseUsageReportSerializer,
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
                hospital, issued_by=request.user, duration_days=d["duration_days"], features=d["features"],
                deployment_id=str(d["deployment_id"] or ""), hardware_binding=d["hardware_binding"],
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
    """Issued on-premise licenses across every hospital: history, renewals
    due, re-download, revoke, and usage reports sent in by hospitals.

    ?status=active|expired|revoked   ?expiring_within=<days>   ?search=<hospital or licence ID>"""

    serializer_class = OnPremiseLicenseSerializer
    permission_classes = [IsAuthenticated, CanViewSaaSTenants]
    queryset = OnPremiseLicense.objects.select_related("hospital", "issued_by").prefetch_related("usage_reports")
    filterset_fields = ["hospital"]

    def get_permissions(self):
        if self.action in ("revoke", "upload_usage_report"):
            return [IsAuthenticated(), CanManageSaaSTenants()]
        return super().get_permissions()

    def get_queryset(self):
        from datetime import timedelta

        from django.db.models import Q

        qs = super().get_queryset()
        params = self.request.query_params
        now = timezone.now()
        state = params.get("status")
        if state == "revoked":
            qs = qs.filter(revoked_at__isnull=False)
        elif state == "expired":
            qs = qs.filter(revoked_at__isnull=True, expires_at__lt=now)
        elif state == "active":
            qs = qs.filter(revoked_at__isnull=True, expires_at__gte=now)
        if params.get("expiring_within", "").isdigit():
            qs = qs.filter(revoked_at__isnull=True, expires_at__gte=now, expires_at__lte=now + timedelta(days=int(params["expiring_within"])))
        if params.get("search"):
            term = params["search"].strip()
            qs = qs.filter(Q(hospital__name__icontains=term) | Q(license_id__icontains=term))
        if self.action == "list" and params.get("ordering") != "-issued_at":
            qs = qs.order_by("expires_at")  # renewals due first
        return qs

    @action(detail=False, methods=["post"], url_path="usage-reports")
    def upload_usage_report(self, request):
        """POST {"report": <usage report file contents>} — a report a
        hospital exported from Settings → License."""
        import json

        from apps.licensing import public_key as bundled
        from apps.licensing.crypto import load_public_key
        from apps.licensing.issuing import SigningKeyMissing, signing_key
        from apps.licensing.usage import verify

        raw = request.data.get("report")
        try:
            document = json.loads(raw) if isinstance(raw, str) else raw
            report = document["report"]
            license_id = report["licence"]["license_id"]
            generated_at = report["generated_at"]
            usage = report["usage"]
        except (ValueError, TypeError, KeyError):
            return Response({"report": ["This isn't a usage report file."]}, status=status.HTTP_400_BAD_REQUEST)
        record = OnPremiseLicense.objects.filter(license_id=license_id).first()
        if record is None:
            return Response({"report": [f"No licence {license_id} was issued from this console."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            key = signing_key().public_key()
        except SigningKeyMissing:
            key = load_public_key(bundled.PUBLIC_KEY_PEM)
        saved = LicenseUsageReport.objects.create(
            license=record, generated_at=generated_at, app_version=str(report.get("app_version", ""))[:64],
            active_users=int(usage.get("active_users") or 0), beds=int(usage.get("beds") or 0),
            report=report, seal_ok=verify(document, key), uploaded_by=request.user,
        )
        return Response(LicenseUsageReportSerializer(saved).data, status=status.HTTP_201_CREATED)

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


def _hospital_admin_or_403(request):
    from apps.accounts.permission_catalog import is_hospital_admin

    user = request.user
    if not user.hospital_id or not (user.is_superuser or is_hospital_admin(user)):
        return Response({"detail": "Only the hospital's administrators can see its subscription."}, status=status.HTTP_403_FORBIDDEN)
    return None


class MySubscriptionView(APIView):
    """GET /subscription/ — the requesting hospital's own plan, usage against
    its limits, and invoices (hospital admins). On-premise installations
    answer {"mode": "on_premise"}: their terms are the licence (Settings → License)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from apps.licensing.service import is_on_premise

        if is_on_premise():
            return Response({"mode": "on_premise"})
        denied = _hospital_admin_or_403(request)
        if denied:
            return denied
        from apps.accounts.models import User
        from apps.core.modules import effective_modules

        hospital = request.user.hospital
        sub = TenantSubscription.objects.filter(hospital=hospital).first()
        invoices = TenantInvoice.objects.filter(hospital=hospital).order_by("-billing_period_start")[:24]
        return Response({
            "mode": "saas",
            "hospital_name": hospital.name,
            "subscription": None if sub is None else {
                "tier": sub.tier, "tier_label": sub.get_tier_display(),
                "billing_cycle": sub.billing_cycle, "billing_cycle_label": sub.get_billing_cycle_display(),
                "status": sub.status, "status_label": sub.get_status_display(),
                "base_price": str(sub.base_price), "started_at": sub.started_at, "next_billing_date": sub.next_billing_date,
                "max_staff_users": sub.max_staff_users,
            },
            "active_users": User.objects.filter(hospital=hospital, is_active=True).count(),
            "enabled_modules": effective_modules(hospital),
            "invoices": [
                {"id": i.id, "invoice_number": i.invoice_number, "billing_period_start": i.billing_period_start,
                 "billing_period_end": i.billing_period_end, "amount": str(i.amount), "status": i.status,
                 "due_date": i.due_date, "paid_at": i.paid_at}
                for i in invoices
            ],
            "outstanding": str(sum((i.amount for i in invoices if i.status != TenantInvoice.Status.PAID), 0)),
        })


class MyInvoicePdfView(APIView):
    """GET /subscription/invoices/<id>/pdf/ — one of the hospital's own invoices."""

    permission_classes = [IsAuthenticated]

    def get(self, request, invoice_id):
        denied = _hospital_admin_or_403(request)
        if denied:
            return denied
        invoice = TenantInvoice.objects.filter(pk=invoice_id, hospital_id=request.user.hospital_id).first()
        if invoice is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        response = HttpResponse(render_invoice_pdf(invoice), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{invoice.invoice_number}.pdf"'
        return response
