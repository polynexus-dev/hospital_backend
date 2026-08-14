from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import ActionPermissionRequired, RequiresClinicalDetailPermission, RoleBasedModelPermissions
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin

from .models import ClinicalNote, Diagnosis, Encounter, InvestigationOrder, VitalsReading
from .serializers import (
    ClinicalNoteSerializer,
    DiagnosisSerializer,
    EncounterSerializer,
    InvestigationOrderSerializer,
    VitalsReadingSerializer,
)

# Every model in this app is clinical content (vitals, notes, diagnoses,
# investigation orders) with no CRM-safe partial view — see
# docs/erp/03-rbac-and-roles.md §2c and
# apps.patients.views.PrescriptionViewSet for the same reasoning applied
# to apps.patients.Prescription. A receptionist role (no
# patients.access_clinical_detail) gets 403 on every endpoint in this app,
# not a filtered view.
CLINICAL_PERMISSION_CLASSES = [IsAuthenticated, RoleBasedModelPermissions, ActionPermissionRequired, RequiresClinicalDetailPermission]


class EncounterViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """Encounters are created automatically at appointment check-in (see
    apps.opd.signals) — this ViewSet is read/admin-correction only in
    practice, but left as a full ModelViewSet rather than ReadOnly so a
    hospital admin can fix a mis-linked encounter without a dbshell."""

    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = EncounterSerializer
    queryset = Encounter.objects.all()
    filterset_fields = ["patient", "doctor", "department", "appointment"]
    assignment_scope_field = "doctor__user"


class VitalsReadingViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = VitalsReadingSerializer
    queryset = VitalsReading.objects.all()
    filterset_fields = ["encounter"]
    assignment_scope_field = "encounter__doctor__user"

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, recorded_by=self.request.user)


class ClinicalNoteViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    action_permissions = {"finalize": "opd.finalize_clinicalnote"}
    audited_fields = ("chief_complaints", "history", "examination_findings")
    serializer_class = ClinicalNoteSerializer
    queryset = ClinicalNote.objects.all()
    filterset_fields = ["encounter", "doctor"]
    assignment_scope_field = "doctor__user"

    def perform_create(self, serializer):
        # doctor is stamped from the encounter, not request.user directly
        # — front-desk-adjacent staff may create the placeholder note
        # before the doctor's own login session picks it up, same as
        # walk-in registration doesn't require the doctor to be the one
        # at the keyboard. See apps.patients.views.PrescriptionViewSet for
        # the contrasting case where doctor *is* stamped from request.user
        # (Prescription.doctor FKs straight to User, not to a Doctor
        # directory entry).
        encounter = serializer.validated_data["encounter"]
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, doctor=encounter.doctor)
        self._log("create", serializer.instance)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        note = self.get_object()
        note.finalize(request.user)
        return Response(ClinicalNoteSerializer(note).data)


class DiagnosisViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    action_permissions = {"finalize": "opd.finalize_diagnosis"}
    audited_fields = ("icd_code", "description", "diagnosis_type")
    serializer_class = DiagnosisSerializer
    queryset = Diagnosis.objects.all()
    filterset_fields = ["encounter", "diagnosis_type"]
    assignment_scope_field = "encounter__doctor__user"

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, created_by=self.request.user)
        self._log("create", serializer.instance)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        diagnosis = self.get_object()
        diagnosis.finalize(request.user)
        return Response(DiagnosisSerializer(diagnosis).data)


class InvestigationOrderViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = InvestigationOrderSerializer
    queryset = InvestigationOrder.objects.all()
    filterset_fields = ["encounter", "order_type", "status"]
    assignment_scope_field = "encounter__doctor__user"

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, created_by=self.request.user)
