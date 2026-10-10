from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import ALL_MODULES, Hospital
from apps.core.permissions import CanManageSaaSBilling, CanManageSaaSSupport, CanManageSaaSTenants, CanViewSaaSAnalytics, CanViewSaaSTenants, IsSaaSAdmin
from apps.core.viewsets import TenantScopedViewSetMixin

from . import services
from .models import LicenseRequest, LicenseUsageReport, OnPremiseLicense, SupportTicket, TenantInvoice, TenantSubscription, TenantUsageSnapshot
from .pdf import render_invoice_pdf
from .serializers import (
    GenerateLicenseSerializer,
    LicenseRequestSerializer,
    LicenseUsageReportSerializer,
    SaaSStaffSerializer,
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
        if self.action == "generate_license":
            return [IsAuthenticated(), IsSaaSAdmin()]  # the licence capability is checked inside
        if self.action in {"create", "update", "partial_update", "destroy", "update_modules", "toggle_status"} or (self.action == "permissions" and self.request.method != "GET"):
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
        """Request an on-premise licence for this hospital (body: the terms
        plus "otp", a current 2FA code). A SaaS Owner's request is approved
        and issued at once — the .lic file, or the record with
        {"response": "json"}. Anyone else's waits for an Owner (202)."""
        from apps.governance import services as gov
        from apps.governance.models import SecurityEvent

        from .licence_controls import describe, notify_owners, otp_error
        from .models import LicenseRequest

        if not request.user.has_saas_capability("license_issue"):
            return Response({"detail": "Only SaaS Owners, and staff a SaaS Owner has authorised, can issue licences."}, status=status.HTTP_403_FORBIDDEN)
        hospital = self.get_object()
        params = GenerateLicenseSerializer(data=request.data)
        params.is_valid(raise_exception=True)
        error = otp_error(request.user, request.data.get("otp"))
        if error:
            return Response({"otp": [error]}, status=status.HTTP_400_BAD_REQUEST)
        terms = {**params.validated_data, "deployment_id": str(params.validated_data["deployment_id"] or "")}
        lic_request = LicenseRequest.objects.create(hospital=hospital, params=terms, requested_by=request.user)
        gov.log_security_event(
            SecurityEvent.EventType.LICENSE_ISSUED, request=request, user=request.user, hospital_id=hospital.pk,
            details={"action": "requested", "request_id": lic_request.pk, "terms": terms},
        )
        if request.user.has_saas_capability("license_approve"):
            record, failure = approve_license_request(lic_request, request.user, request)
            if failure:
                return failure
            if request.data.get("response") == "json":
                return Response(OnPremiseLicenseSerializer(record).data, status=status.HTTP_201_CREATED)
            return license_file_response(record)
        notify_owners(
            f"Approval needed: licence for {hospital.name}",
            f"{request.user.staff_code} ({request.user.email}) requested a licence.\n\n{describe(terms, hospital)}\n"
            "Approve or reject it in the SaaS console: On-Premise Licences.",
        )
        return Response({"detail": "Sent to a SaaS Owner for approval.", "request": LicenseRequestSerializer(lic_request).data},
                        status=status.HTTP_202_ACCEPTED)

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
        if self.action == "revoke":
            return [IsAuthenticated(), LicenceApprover()]
        if self.action in ("upload_usage_report", "revocation_list"):
            return [IsAuthenticated(), LicenceIssuer()]
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
            qs = qs.filter(Q(hospital__name__icontains=term) | Q(license_id__icontains=term) | Q(issued_by__staff_code__iexact=term))
        if params.get("issued_by"):
            qs = qs.filter(issued_by__staff_code__iexact=params["issued_by"].strip())
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
        from apps.governance import services as gov
        from apps.governance.models import SecurityEvent

        from .licence_controls import notify_owners

        record = OnPremiseLicense.objects.filter(license_id=license_id).first()
        if record is None:
            # A licence in use that this console never issued: forged, or
            # issued outside the approval process. Raise it with the Owners.
            details = {"license_id": license_id, "hospitals": report.get("hospitals"), "issued_by_in_file": report["licence"].get("issued_by"),
                       "deployment_id": report["licence"].get("deployment_id")}
            gov.log_security_event(SecurityEvent.EventType.LICENSE_ALERT, request=request, user=request.user, details={"alert": "unknown_licence", **details})
            notify_owners(f"ALERT: unknown licence {license_id} in use",
                          f"A usage report names licence {license_id}, which this console never issued.\n{details}")
            return Response({"report": [f"ALERT: licence {license_id} was never issued from this console. The SaaS Owners have been notified."]},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            key = signing_key().public_key()
        except SigningKeyMissing:
            key = load_public_key(bundled.PUBLIC_KEY_PEM)
        saved = LicenseUsageReport.objects.create(
            license=record, generated_at=generated_at, app_version=str(report.get("app_version", ""))[:64],
            active_users=int(usage.get("active_users") or 0), beds=int(usage.get("beds") or 0),
            report=report, seal_ok=verify(document, key), uploaded_by=request.user,
        )
        if not saved.seal_ok:
            gov.log_security_event(SecurityEvent.EventType.LICENSE_ALERT, request=request, user=request.user, hospital_id=record.hospital_id,
                                   details={"alert": "edited_usage_report", "license_id": license_id, "report_id": saved.pk})
        return Response(LicenseUsageReportSerializer(saved).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        record = self.get_object()
        if record.revoked_at:
            return Response({"detail": "This license has been revoked."}, status=status.HTTP_410_GONE)
        return license_file_response(record)

    @action(detail=False, methods=["get"], url_path="revocation-list")
    def revocation_list(self, request):
        """The signed list of revoked licences. Save it as
        Backend/apps/licensing/revocations.lic before building a release, so
        upgraded installations stop accepting those licences."""
        from apps.licensing.issuing import SigningKeyMissing

        from .licence_controls import revocation_document

        try:
            document = revocation_document()
        except SigningKeyMissing as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        response = HttpResponse(document + "\n", content_type="application/octet-stream")
        response["Content-Disposition"] = 'attachment; filename="revocations.lic"'
        return response

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
                SecurityEvent.EventType.LICENSE_ISSUED, request=request, user=request.user, hospital_id=record.hospital_id,
                details={"action": "revoked", "license_id": record.license_id, "reason": record.revoke_reason},
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


class LicenceIssuer(BasePermission):
    """SaaS Owners, and staff an Owner authorised to issue licences."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.has_saas_capability("license_issue"))


class LicenceApprover(BasePermission):
    """SaaS Owners only: approving, revoking, and managing who may issue."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.has_saas_capability("license_approve"))


def approve_license_request(lic_request, approver, request):
    """Sign and record the licence for an approved request. Returns
    (record, None), or (None, error Response)."""
    from apps.governance import services as gov
    from apps.governance.models import SecurityEvent
    from apps.licensing.issuing import SigningKeyMissing, issue_license

    from .licence_controls import describe, notify_owners

    t = lic_request.params
    try:
        record = issue_license(
            lic_request.hospital, issued_by=lic_request.requested_by, approved_by=approver,
            duration_days=t["duration_days"], features=t["features"], deployment_id=t.get("deployment_id") or "",
            hardware_binding=t["hardware_binding"], machine_fingerprint=t.get("machine_fingerprint") or "",
            max_active_users=t["max_users"], max_beds=t["max_beds"], grace_period_days=t["grace_period_days"], tier=t.get("tier") or "",
        )
    except SigningKeyMissing as exc:
        return None, Response({"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    lic_request.status = lic_request.Status.APPROVED
    lic_request.decided_by = approver
    lic_request.decided_at = timezone.now()
    lic_request.license = record
    lic_request.save(update_fields=["status", "decided_by", "decided_at", "license", "updated_at"])
    gov.log_security_event(
        SecurityEvent.EventType.LICENSE_ISSUED, request=request, user=approver, hospital_id=record.hospital_id,
        details={"action": "issued", "license_id": record.license_id, "request_id": lic_request.pk,
                 "issued_by": lic_request.requested_by.staff_code, "approved_by": approver.staff_code},
    )
    notify_owners(
        f"Licence issued: {record.license_id} for {record.hospital.name}",
        f"Requested by {lic_request.requested_by.staff_code}, approved by {approver.staff_code}.\n\n{describe(t, record.hospital)}",
    )
    return record, None


class LicenseRequestViewSet(viewsets.ReadOnlyModelViewSet):
    """Licence requests. Owners see all and approve or reject; licence
    managers see their own and can cancel them while pending."""

    serializer_class = LicenseRequestSerializer
    permission_classes = [IsAuthenticated, LicenceIssuer]
    queryset = LicenseRequest.objects.select_related("hospital", "requested_by", "decided_by", "license")
    filterset_fields = ["status", "hospital"]

    def get_queryset(self):
        qs = super().get_queryset()
        if not self.request.user.has_saas_capability("license_approve"):
            qs = qs.filter(requested_by=self.request.user)
        return qs

    def get_permissions(self):
        if self.action in ("approve", "reject"):
            return [IsAuthenticated(), LicenceApprover()]
        return super().get_permissions()

    def _pending(self):
        lic_request = self.get_object()
        if lic_request.status != LicenseRequest.Status.PENDING:
            return lic_request, Response({"detail": f"This request is already {lic_request.get_status_display().lower()}."}, status=status.HTTP_400_BAD_REQUEST)
        return lic_request, None

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """POST {"otp": "123456"} — sign and issue the licence."""
        from .licence_controls import otp_error

        lic_request, problem = self._pending()
        if problem:
            return problem
        error = otp_error(request.user, request.data.get("otp"))
        if error:
            return Response({"otp": [error]}, status=status.HTTP_400_BAD_REQUEST)
        if lic_request.requested_by.is_blocked or not lic_request.requested_by.is_active:
            return Response({"detail": "The requester's account is blocked. Reject this request."}, status=status.HTTP_400_BAD_REQUEST)
        record, failure = approve_license_request(lic_request, request.user, request)
        if failure:
            return failure
        return Response({"request": LicenseRequestSerializer(lic_request).data, "license": OnPremiseLicenseSerializer(record).data})

    def _close(self, request, new_status, action_name):
        from apps.governance import services as gov
        from apps.governance.models import SecurityEvent

        lic_request, problem = self._pending()
        if problem:
            return problem
        lic_request.status = new_status
        lic_request.decided_by = request.user
        lic_request.decided_at = timezone.now()
        lic_request.decision_note = str(request.data.get("note", ""))[:255]
        lic_request.save(update_fields=["status", "decided_by", "decided_at", "decision_note", "updated_at"])
        gov.log_security_event(SecurityEvent.EventType.LICENSE_ISSUED, request=request, user=request.user, hospital_id=lic_request.hospital_id,
                               details={"action": action_name, "request_id": lic_request.pk, "note": lic_request.decision_note})
        return Response(LicenseRequestSerializer(lic_request).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        return self._close(request, LicenseRequest.Status.REJECTED, "rejected")

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        lic_request = self.get_object()
        if lic_request.requested_by_id != request.user.pk:
            return Response({"detail": "Only the person who requested it can cancel it."}, status=status.HTTP_403_FORBIDDEN)
        return self._close(request, LicenseRequest.Status.CANCELLED, "cancelled")


class SaaSStaffViewSet(viewsets.ReadOnlyModelViewSet):
    """Polynexus staff, for SaaS Owners: who may issue licences, and blocking
    someone who leaves (ends their sessions at once and removes the right)."""

    serializer_class = SaaSStaffSerializer
    permission_classes = [IsAuthenticated, LicenceApprover]

    def get_queryset(self):
        from apps.accounts.models import User

        return User.objects.filter(is_saas_admin=True).order_by("staff_code")

    @action(detail=True, methods=["post"], url_path="licence-right")
    def licence_right(self, request, pk=None):
        """POST {"grant": true|false, "otp": "123456"}"""
        from apps.governance import services as gov
        from apps.governance.models import SecurityEvent

        from .licence_controls import otp_error

        error = otp_error(request.user, request.data.get("otp"))
        if error:
            return Response({"otp": [error]}, status=status.HTTP_400_BAD_REQUEST)
        staff = self.get_object()
        grant = bool(request.data.get("grant"))
        if grant and staff.is_blocked:
            return Response({"detail": "Unblock the account first."}, status=status.HTTP_400_BAD_REQUEST)
        staff.can_issue_licenses = grant
        staff.save(update_fields=["can_issue_licenses"])
        gov.log_security_event(SecurityEvent.EventType.LICENSE_ISSUED, request=request, user=request.user,
                               details={"action": "right_granted" if grant else "right_removed", "staff": staff.staff_code})
        return Response(SaaSStaffSerializer(staff).data)

    @action(detail=True, methods=["post"])
    def block(self, request, pk=None):
        """POST {"reason": "...", "otp": "123456"} — for someone leaving:
        ends every session now, removes the licence right, cancels their
        pending requests. Licences they issued stay listed under their code."""
        from apps.accounts.authentication import end_all_sessions
        from apps.governance import services as gov
        from apps.governance.models import SecurityEvent

        from .licence_controls import notify_owners, otp_error

        error = otp_error(request.user, request.data.get("otp"))
        if error:
            return Response({"otp": [error]}, status=status.HTTP_400_BAD_REQUEST)
        staff = self.get_object()
        if staff.pk == request.user.pk:
            return Response({"detail": "You can't block your own account."}, status=status.HTTP_400_BAD_REQUEST)
        staff.is_blocked = True
        staff.blocked_reason = str(request.data.get("reason", ""))[:255] or "Left Polynexus"
        staff.can_issue_licenses = False
        staff.save(update_fields=["is_blocked", "blocked_reason", "can_issue_licenses"])
        ended = end_all_sessions(staff)
        cancelled = LicenseRequest.objects.filter(requested_by=staff, status=LicenseRequest.Status.PENDING).update(
            status=LicenseRequest.Status.CANCELLED, decided_by=request.user, decided_at=timezone.now(), decision_note="Requester blocked")
        gov.log_security_event(SecurityEvent.EventType.USER_BLOCKED, request=request, user=staff,
                               details={"by": request.user.staff_code, "reason": staff.blocked_reason, "sessions_ended": ended, "requests_cancelled": cancelled})
        notify_owners(f"Staff blocked: {staff.staff_code}", f"{staff.email} was blocked by {request.user.staff_code}: {staff.blocked_reason}")
        return Response(SaaSStaffSerializer(staff).data)

    @action(detail=True, methods=["post"])
    def unblock(self, request, pk=None):
        from apps.governance import services as gov
        from apps.governance.models import SecurityEvent

        staff = self.get_object()
        staff.is_blocked = False
        staff.blocked_reason = ""
        staff.save(update_fields=["is_blocked", "blocked_reason"])
        gov.log_security_event(SecurityEvent.EventType.USER_UNBLOCKED, request=request, user=staff, details={"by": request.user.staff_code})
        return Response(SaaSStaffSerializer(staff).data)
