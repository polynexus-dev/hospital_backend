from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import ActionPermissionRequired, RequiresClinicalDetailPermission, RoleBasedModelPermissions
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin

from .models import (
    AnaesthesiaRecord,
    ConsumableUsage,
    ImplantUsage,
    OperativeNote,
    OTSchedule,
    PreOpChecklist,
    SurgeryRequest,
)
from .serializers import (
    AnaesthesiaRecordSerializer,
    ConsumableUsageSerializer,
    ImplantUsageSerializer,
    OperativeNoteSerializer,
    OTScheduleSerializer,
    PreOpChecklistSerializer,
    SurgeryRequestSerializer,
)

CLINICAL_PERMISSION_CLASSES = [IsAuthenticated, RoleBasedModelPermissions, ActionPermissionRequired, RequiresClinicalDetailPermission]


class SurgeryRequestViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = SurgeryRequestSerializer
    queryset = SurgeryRequest.objects.all()
    filterset_fields = ["patient", "admission", "status"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, requested_by=self.request.user)


class OTScheduleViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = OTScheduleSerializer
    queryset = OTSchedule.objects.all()
    filterset_fields = ["surgery_request", "surgeon", "operation_theatre_room"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        instance = serializer.save(hospital=hospital)
        instance.surgery_request.status = SurgeryRequest.Status.SCHEDULED
        instance.surgery_request.save(update_fields=["status"])


class PreOpChecklistViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = PreOpChecklistSerializer
    queryset = PreOpChecklist.objects.all()
    filterset_fields = ["surgery_request"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, completed_by=self.request.user)


class OperativeNoteViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    action_permissions = {"finalize": "ot.finalize_operativenote"}
    audited_fields = ("procedure_performed", "findings")
    serializer_class = OperativeNoteSerializer
    queryset = OperativeNote.objects.all()
    filterset_fields = ["ot_schedule", "surgeon"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        note = self.get_object()
        note.finalize(request.user)
        return Response(self.get_serializer(note).data)


class AnaesthesiaRecordViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    action_permissions = {"finalize": "ot.finalize_anaesthesiarecord"}
    audited_fields = ("anaesthesia_type", "intra_op_notes")
    serializer_class = AnaesthesiaRecordSerializer
    queryset = AnaesthesiaRecord.objects.all()
    filterset_fields = ["ot_schedule", "anaesthetist"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        record = self.get_object()
        record.finalize(request.user)
        return Response(self.get_serializer(record).data)


class ConsumableUsageViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = ConsumableUsageSerializer
    queryset = ConsumableUsage.objects.all()
    filterset_fields = ["ot_schedule"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)


class ImplantUsageViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = ImplantUsageSerializer
    queryset = ImplantUsage.objects.all()
    filterset_fields = ["ot_schedule"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)
