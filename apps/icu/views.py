from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import ActionPermissionRequired, RequiresClinicalDetailPermission, RoleBasedModelPermissions
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin

from .models import ICUAdmission, ICUDailyProgressNote, VentilatorLog
from .serializers import ICUAdmissionSerializer, ICUDailyProgressNoteSerializer, VentilatorLogSerializer

CLINICAL_PERMISSION_CLASSES = [IsAuthenticated, RoleBasedModelPermissions, ActionPermissionRequired, RequiresClinicalDetailPermission]


class ICUAdmissionViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = ICUAdmissionSerializer
    queryset = ICUAdmission.objects.all()
    filterset_fields = ["admission", "bed", "ventilator_required"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)


class VentilatorLogViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = VentilatorLogSerializer
    queryset = VentilatorLog.objects.all()
    filterset_fields = ["icu_admission"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, recorded_by=self.request.user)


class ICUDailyProgressNoteViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    action_permissions = {"finalize": "icu.finalize_icudailyprogressnote"}
    audited_fields = ("note",)
    serializer_class = ICUDailyProgressNoteSerializer
    queryset = ICUDailyProgressNote.objects.all()
    filterset_fields = ["icu_admission", "doctor"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        note = self.get_object()
        note.finalize(request.user)
        return Response(self.get_serializer(note).data)
