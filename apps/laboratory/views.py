from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import ActionPermissionRequired, RequiresClinicalDetailPermission, RoleBasedModelPermissions
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin

from .models import LabOrder, LabResult, LabTest, LabTestPackage, SampleCollection
from .serializers import (
    LabOrderSerializer,
    LabResultSerializer,
    LabTestPackageSerializer,
    LabTestSerializer,
    SampleCollectionSerializer,
)

# Same reasoning as apps.opd.views.CLINICAL_PERMISSION_CLASSES — lab
# orders/results are clinical content, no CRM-safe partial view. LabTest/
# LabTestPackage (the catalogue) are the exception — see their ViewSets below.
CLINICAL_PERMISSION_CLASSES = [IsAuthenticated, RoleBasedModelPermissions, ActionPermissionRequired, RequiresClinicalDetailPermission]


class LabTestViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """Catalogue data, not patient content — no access_clinical_detail gate,
    same reasoning as apps.packages.HealthPackageViewSet: every front-desk/
    billing role needs to see what a test costs to quote or bill it."""

    serializer_class = LabTestSerializer
    queryset = LabTest.objects.all()
    filterset_fields = ["department", "is_active"]
    search_fields = ["name", "code"]


class LabTestPackageViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = LabTestPackageSerializer
    queryset = LabTestPackage.objects.all()
    filterset_fields = ["is_active"]
    search_fields = ["name"]


class LabOrderViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = LabOrderSerializer
    queryset = LabOrder.objects.all()
    filterset_fields = ["patient", "status", "investigation_order"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, ordered_by=self.request.user)


class SampleCollectionViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = SampleCollectionSerializer
    queryset = SampleCollection.objects.all()
    filterset_fields = ["lab_order"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        collection = serializer.save(hospital=hospital, collected_by=self.request.user)
        lab_order = collection.lab_order
        if lab_order.status == LabOrder.Status.ORDERED:
            lab_order.status = LabOrder.Status.SAMPLE_COLLECTED
            lab_order.save(update_fields=["status"])


class LabResultViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    action_permissions = {"verify": "laboratory.verify_labresult"}
    audited_fields = ("value", "flag")
    serializer_class = LabResultSerializer
    queryset = LabResult.objects.all()
    filterset_fields = ["lab_order", "lab_test", "flag"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, entered_by=self.request.user)
        self._log("create", serializer.instance)
        self._sync_order_status_to_resulted(serializer.instance.lab_order)
        self._maybe_alert_critical(serializer.instance)

    def perform_update(self, serializer):
        super().perform_update(serializer)
        self._maybe_alert_critical(serializer.instance)

    @staticmethod
    def _sync_order_status_to_resulted(lab_order):
        if lab_order.status in (LabOrder.Status.ORDERED, LabOrder.Status.SAMPLE_COLLECTED, LabOrder.Status.PROCESSING):
            lab_order.status = LabOrder.Status.RESULTED
            lab_order.save(update_fields=["status"])

    @staticmethod
    def _maybe_alert_critical(instance):
        if instance.flag == LabResult.Flag.CRITICAL:
            from .signals import result_critical
            result_critical.send(sender=LabResult, result=instance)

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        result = self.get_object()
        result.finalize(request.user)

        lab_order = result.lab_order
        if not lab_order.results.exclude(finalized_at__isnull=False).exists():
            lab_order.status = LabOrder.Status.VERIFIED
            lab_order.save(update_fields=["status"])

        return Response(LabResultSerializer(result).data)
