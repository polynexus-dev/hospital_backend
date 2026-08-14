from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.appointments.models import Doctor
from apps.core.models import Department
from apps.core.permissions import ActionPermissionRequired, RequiresClinicalDetailPermission, RoleBasedModelPermissions
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin
from apps.facilities.models import Bed
from apps.opd.models import Encounter
from apps.patients.models import Patient

from .models import Admission, BedAllocation, DischargeSummary, DoctorProgressNote, WardTransfer
from .serializers import (
    AdmissionSerializer,
    AdmitPatientSerializer,
    BedAllocationSerializer,
    DischargeAdmissionSerializer,
    DischargeSummarySerializer,
    DoctorProgressNoteSerializer,
    WardTransferSerializer,
)
from .services import (
    BedUnavailable,
    DischargeSummaryRequired,
    admit_patient,
    approve_ward_transfer,
    discharge_patient,
    request_ward_transfer,
)

# Same reasoning as apps.opd.views.CLINICAL_PERMISSION_CLASSES — everything
# here is clinical content, no CRM-safe partial view.
CLINICAL_PERMISSION_CLASSES = [IsAuthenticated, RoleBasedModelPermissions, ActionPermissionRequired, RequiresClinicalDetailPermission]


class AdmissionViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    action_permissions = {"discharge": "ipd.change_admission"}
    serializer_class = AdmissionSerializer
    queryset = Admission.objects.all()
    filterset_fields = ["patient", "admitting_doctor", "department", "status"]
    assignment_scope_field = "admitting_doctor__user"

    def create(self, request, *args, **kwargs):
        serializer = AdmitPatientSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        hospital = getattr(request.user, "hospital", None)
        patient = get_object_or_404(Patient, pk=data["patient"], hospital=hospital)
        admitting_doctor = get_object_or_404(Doctor, pk=data["admitting_doctor"], hospital=hospital)
        bed = get_object_or_404(Bed, pk=data["bed"], hospital=hospital)
        department = get_object_or_404(Department, pk=data["department"]) if data.get("department") else None
        source_encounter = get_object_or_404(Encounter, pk=data["source_encounter"], hospital=hospital) if data.get("source_encounter") else None

        try:
            admission = admit_patient(
                hospital=hospital, patient=patient, admitting_doctor=admitting_doctor, bed=bed,
                admission_type=data["admission_type"], department=department,
                admission_diagnosis=data["admission_diagnosis"], source_encounter=source_encounter,
            )
        except BedUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(AdmissionSerializer(admission).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def discharge(self, request, pk=None):
        admission = self.get_object()
        serializer = DischargeAdmissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            discharged = discharge_patient(admission, status=serializer.validated_data["status"])
        except DischargeSummaryRequired as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(AdmissionSerializer(discharged).data)


class BedAllocationViewSet(TenantScopedViewSetMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = BedAllocationSerializer
    queryset = BedAllocation.objects.all()
    filterset_fields = ["admission", "bed"]


class WardTransferViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    action_permissions = {"approve": "ipd.change_wardtransfer"}
    serializer_class = WardTransferSerializer
    queryset = WardTransfer.objects.all()
    filterset_fields = ["admission"]

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        admission = serializer.validated_data["admission"]
        transfer = request_ward_transfer(
            admission=admission, to_bed=serializer.validated_data["to_bed"],
            reason=serializer.validated_data.get("reason", ""), requested_by=self.request.user,
        )
        serializer.instance = transfer

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        transfer = self.get_object()
        try:
            approved = approve_ward_transfer(transfer, approved_by=request.user)
        except BedUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(WardTransferSerializer(approved).data)


class DoctorProgressNoteViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    action_permissions = {"finalize": "ipd.finalize_doctorprogressnote"}
    audited_fields = ("note",)
    serializer_class = DoctorProgressNoteSerializer
    queryset = DoctorProgressNote.objects.all()
    filterset_fields = ["admission", "doctor"]
    assignment_scope_field = "doctor__user"

    def perform_create(self, serializer):
        # doctor stamped from the admission, same reasoning as
        # apps.opd.views.ClinicalNoteViewSet — front-desk-adjacent staff
        # may create the entry before the doctor's own session picks it up.
        admission = serializer.validated_data["admission"]
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, doctor=admission.admitting_doctor)
        self._log("create", serializer.instance)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        note = self.get_object()
        note.finalize(request.user)
        return Response(DoctorProgressNoteSerializer(note).data)


class DischargeSummaryViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    action_permissions = {"finalize": "ipd.finalize_dischargesummary"}
    audited_fields = ("final_diagnosis", "procedures_performed", "treatment_summary", "discharge_medications", "follow_up_instructions", "discharge_type")
    serializer_class = DischargeSummarySerializer
    queryset = DischargeSummary.objects.all()
    filterset_fields = ["admission"]
    assignment_scope_field = "admission__admitting_doctor__user"

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, prepared_by=self.request.user)
        self._log("create", serializer.instance)

    @action(detail=True, methods=["post"])
    def finalize(self, request, pk=None):
        summary = self.get_object()
        summary.finalize(request.user)
        return Response(DischargeSummarySerializer(summary).data)
