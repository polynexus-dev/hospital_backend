from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import RequiresViewPermission, RoleBasedModelPermissions
from apps.core.viewsets import AuditedModelViewSetMixin, SoftDeleteViewSetMixin, TenantScopedViewSetMixin

from .models import ConsentNotice, ConsentPurpose, DataRightsRequest, GrievanceTicket, Nominee, PatientConsent
from .serializers import ConsentNoticeSerializer, DataRightsRequestSerializer, GrievanceTicketSerializer, NomineeSerializer, PatientConsentSerializer
from .services import collect_patient_data, complete_erasure, current_consents, sync_messaging_opt_out

# DPDP Act 2023 data-rights/grievance/nominee records are restricted to
# owner/admin/hospital_administrator by design (see
# apps.accounts.permission_templates.PERMISSION_TEMPLATES — "privacy" only
# ever appears in FULL_ACCESS_APPS), but RoleBasedModelPermissions alone
# doesn't enforce that for list/retrieve — see RequiresViewPermission.
PRIVACY_PERMISSION_CLASSES = [IsAuthenticated, RoleBasedModelPermissions, RequiresViewPermission]


class DataRightsRequestViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = DataRightsRequestSerializer
    # select_related: DataRightsRequestSerializer.patient_name (source="patient.full_name")
    queryset = DataRightsRequest.objects.select_related("patient")
    permission_classes = PRIVACY_PERMISSION_CLASSES
    filterset_fields = ["patient", "request_type", "status"]

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        """Confirms the requester's identity is who they claim (Part A #11
        implicitly requires this — honoring an erasure/access request from
        whoever merely phones in claiming to be the patient would itself
        be a data-protection failure)."""
        req = self.get_object()
        req.verified_at = timezone.now()
        req.verified_by = request.user
        req.status = DataRightsRequest.Status.VERIFIED
        req.save(update_fields=["verified_at", "verified_by", "status"])
        return Response(DataRightsRequestSerializer(req).data)

    @action(detail=True, methods=["get"])
    def export(self, request, pk=None):
        """The "right to access" itself — only for a verified ACCESS
        request, so an unverified request can't be used to exfiltrate a
        patient's full record."""
        req = self.get_object()
        if req.request_type != DataRightsRequest.RequestType.ACCESS:
            return Response({"detail": "Only an 'access' request can be exported."}, status=status.HTTP_400_BAD_REQUEST)
        if req.status not in (DataRightsRequest.Status.VERIFIED, DataRightsRequest.Status.IN_PROGRESS, DataRightsRequest.Status.COMPLETED):
            return Response({"detail": "Verify the requester's identity before exporting their data."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(collect_patient_data(req.patient))

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        req = self.get_object()
        if req.status not in (DataRightsRequest.Status.VERIFIED, DataRightsRequest.Status.IN_PROGRESS):
            return Response({"detail": "Verify the request before completing it."}, status=status.HTTP_400_BAD_REQUEST)

        if req.request_type == DataRightsRequest.RequestType.ERASURE:
            complete_erasure(req, actor=request.user)

        req.status = DataRightsRequest.Status.COMPLETED
        req.handled_by = request.user
        req.resolution_notes = str(request.data.get("resolution_notes", ""))
        req.resolved_at = timezone.now()
        req.save(update_fields=["status", "handled_by", "resolution_notes", "resolved_at"])
        return Response(DataRightsRequestSerializer(req).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        req = self.get_object()
        req.status = DataRightsRequest.Status.REJECTED
        req.handled_by = request.user
        req.resolution_notes = str(request.data.get("resolution_notes", ""))
        req.resolved_at = timezone.now()
        req.save(update_fields=["status", "handled_by", "resolution_notes", "resolved_at"])
        return Response(DataRightsRequestSerializer(req).data)


class GrievanceTicketViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = GrievanceTicketSerializer
    # select_related: GrievanceTicketSerializer.patient_name (source="patient.full_name")
    queryset = GrievanceTicket.objects.select_related("patient")
    permission_classes = PRIVACY_PERMISSION_CLASSES
    filterset_fields = ["patient", "status", "priority", "assigned_to"]

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        ticket = self.get_object()
        ticket.status = GrievanceTicket.Status.RESOLVED
        ticket.resolution = str(request.data.get("resolution", ""))
        ticket.resolved_at = timezone.now()
        ticket.save(update_fields=["status", "resolution", "resolved_at"])
        return Response(GrievanceTicketSerializer(ticket).data)


class NomineeViewSet(SoftDeleteViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = NomineeSerializer
    queryset = Nominee.objects.all()
    permission_classes = PRIVACY_PERMISSION_CLASSES
    filterset_fields = ["patient", "is_active"]

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        nominee = self.get_object()
        nominee.verified_at = timezone.now()
        nominee.verified_by = request.user
        nominee.save(update_fields=["verified_at", "verified_by"])
        return Response(NomineeSerializer(nominee).data)


# --- DPDP consent register ------------------------------------------------------


class CanPublishNotices(BasePermission):
    """Anyone in the hospital can read the notices (they're shown to
    patients at registration); only privacy administrators publish them."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return request.user.has_perm("privacy.add_consentnotice")


class CanRecordConsent(BasePermission):
    """Front desk records consent at registration, so this follows the
    patient record permissions rather than the admin-only privacy app."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        return request.user.has_perm("patients.view_patient" if request.method in SAFE_METHODS else "patients.change_patient")


class ConsentNoticeViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = ConsentNoticeSerializer
    queryset = ConsentNotice.objects.all()
    permission_classes = [CanPublishNotices]
    filterset_fields = ["purpose", "language", "is_active"]
    http_method_names = ["get", "post", "patch", "head", "options"]  # notices are retired, never deleted

    def perform_create(self, serializer):
        serializer.save(hospital=self.request.user.hospital, published_by=self.request.user)


class PatientConsentViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = PatientConsentSerializer
    queryset = PatientConsent.objects.select_related("notice", "captured_by")
    permission_classes = [CanRecordConsent]
    filterset_fields = ["patient", "purpose", "status"]
    http_method_names = ["get", "post", "head", "options"]  # recorded once; withdrawal is its own action

    def perform_create(self, serializer):
        consent = serializer.save(hospital=self.request.user.hospital, captured_by=self.request.user)
        sync_messaging_opt_out(consent, self.request.user)

    @action(detail=True, methods=["post"])
    def withdraw(self, request, pk=None):
        """POST {"reason": "..."}. DPDP section 6(4): withdrawing must be as easy as consenting."""
        consent = self.get_object()
        if consent.status != PatientConsent.Status.GRANTED:
            return Response({"detail": "Only a granted consent can be withdrawn."}, status=status.HTTP_400_BAD_REQUEST)
        consent.status = PatientConsent.Status.WITHDRAWN
        consent.withdrawn_at = timezone.now()
        consent.withdrawn_by = request.user
        consent.withdrawal_reason = str(request.data.get("reason", ""))[:255]
        consent.save(update_fields=["status", "withdrawn_at", "withdrawn_by", "withdrawal_reason", "updated_at"])
        self._log("update", consent)
        sync_messaging_opt_out(consent, request.user)
        return Response(PatientConsentSerializer(consent).data)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """GET ?patient=<id>: the current answer for every purpose, and the
        notice to show for any purpose not asked yet (or asked under an
        older version of the notice)."""
        from apps.patients.models import Patient

        patient = Patient.objects.filter(pk=request.query_params.get("patient"), hospital_id=request.user.hospital_id).first()
        if patient is None:
            return Response({"detail": "Patient not found."}, status=status.HTTP_404_NOT_FOUND)
        language = request.query_params.get("language", "en")
        notices = {n.purpose: n for n in ConsentNotice.objects.filter(hospital_id=patient.hospital_id, is_active=True, language=language)}
        rows = []
        for purpose, consent in current_consents(patient).items():
            notice = notices.get(purpose)
            rows.append({
                "purpose": purpose,
                "purpose_label": ConsentPurpose(purpose).label,
                "consent": PatientConsentSerializer(consent).data if consent else None,
                "current_notice": ConsentNoticeSerializer(notice).data if notice else None,
                "outdated": bool(consent and notice and consent.notice_id != notice.id and consent.status == PatientConsent.Status.GRANTED),
            })
        return Response(rows)
