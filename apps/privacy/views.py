from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import RequiresViewPermission, RoleBasedModelPermissions
from apps.core.viewsets import SoftDeleteViewSetMixin, TenantScopedViewSetMixin

from .models import DataRightsRequest, GrievanceTicket, Nominee
from .serializers import DataRightsRequestSerializer, GrievanceTicketSerializer, NomineeSerializer
from .services import collect_patient_data, complete_erasure

# DPDP Act 2023 data-rights/grievance/nominee records are restricted to
# owner/admin/hospital_administrator by design (see
# apps.accounts.permission_templates.PERMISSION_TEMPLATES — "privacy" only
# ever appears in FULL_ACCESS_APPS), but RoleBasedModelPermissions alone
# doesn't enforce that for list/retrieve — see RequiresViewPermission.
PRIVACY_PERMISSION_CLASSES = [IsAuthenticated, RoleBasedModelPermissions, RequiresViewPermission]


class DataRightsRequestViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = DataRightsRequestSerializer
    queryset = DataRightsRequest.objects.all()
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
    queryset = GrievanceTicket.objects.all()
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
