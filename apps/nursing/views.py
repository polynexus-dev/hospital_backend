from django.contrib.contenttypes.models import ContentType
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import ActionPermissionRequired, RequiresClinicalDetailPermission, RoleBasedModelPermissions
from apps.core.viewsets import TenantScopedViewSetMixin
from apps.ipd.models import Admission

from .models import IntakeOutput, MedicationAdministration, NursingNote
from .serializers import IntakeOutputSerializer, MedicationAdministrationSerializer, NursingNoteSerializer

CLINICAL_PERMISSION_CLASSES = [IsAuthenticated, RoleBasedModelPermissions, ActionPermissionRequired, RequiresClinicalDetailPermission]


class AdmissionFilteredMixin:
    """?admission=<id> — a plain filterset_fields entry can't reach into a
    GenericForeignKey's content_type/object_id pair, so this resolves the
    filter explicitly instead. Assumes Admission since that's the only
    content type nursing content can target until Phase 6."""

    def get_queryset(self):
        queryset = super().get_queryset()
        admission_id = self.request.query_params.get("admission")
        if admission_id:
            queryset = queryset.filter(content_type=ContentType.objects.get_for_model(Admission), object_id=str(admission_id))
        return queryset


class NursingNoteViewSet(AdmissionFilteredMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = NursingNoteSerializer
    queryset = NursingNote.objects.all()

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, nurse=self.request.user)


class MedicationAdministrationViewSet(AdmissionFilteredMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = MedicationAdministrationSerializer
    queryset = MedicationAdministration.objects.all()

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, nurse=self.request.user)


class IntakeOutputViewSet(AdmissionFilteredMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = IntakeOutputSerializer
    queryset = IntakeOutput.objects.all()

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, recorded_by=self.request.user)
