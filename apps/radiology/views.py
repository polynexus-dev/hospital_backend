from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import ActionPermissionRequired, RequiresClinicalDetailPermission, RoleBasedModelPermissions
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin

from .models import RadiologyOrder, RadiologyProcedure, RadiologyReport
from .serializers import RadiologyOrderSerializer, RadiologyProcedureSerializer, RadiologyReportSerializer

CLINICAL_PERMISSION_CLASSES = [IsAuthenticated, RoleBasedModelPermissions, ActionPermissionRequired, RequiresClinicalDetailPermission]


class RadiologyProcedureViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """Catalogue data, same reasoning as apps.laboratory.LabTestViewSet —
    no clinical-detail gate, front-desk/billing need to see procedure prices."""

    serializer_class = RadiologyProcedureSerializer
    queryset = RadiologyProcedure.objects.all()
    filterset_fields = ["modality", "is_active"]
    search_fields = ["name"]


class RadiologyOrderViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = RadiologyOrderSerializer
    queryset = RadiologyOrder.objects.all()
    filterset_fields = ["patient", "procedure", "status"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, ordered_by=self.request.user)


class RadiologyReportViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    action_permissions = {"verify": "radiology.verify_radiologyreport"}
    audited_fields = ("findings", "impression")
    serializer_class = RadiologyReportSerializer
    queryset = RadiologyReport.objects.all()
    filterset_fields = ["radiology_order"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        report = serializer.save(hospital=hospital, reported_by=self.request.user)
        self._log("create", report)

        order = report.radiology_order
        if order.status != RadiologyOrder.Status.REPORTED:
            order.status = RadiologyOrder.Status.REPORTED
            order.save(update_fields=["status"])

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        report = self.get_object()
        report.finalize(request.user)
        return Response(RadiologyReportSerializer(report).data)
