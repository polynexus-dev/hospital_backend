from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import ActionPermissionRequired, RequiresClinicalDetailPermission, RoleBasedModelPermissions
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin
from apps.ipd.models import Admission
from apps.ipd.serializers import AdmissionSerializer

from .models import EDVisit, Triage
from .serializers import EDVisitSerializer, TriageSerializer
from .signals import admission_required

CLINICAL_PERMISSION_CLASSES = [IsAuthenticated, RoleBasedModelPermissions, ActionPermissionRequired, RequiresClinicalDetailPermission]


class EDVisitViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = EDVisitSerializer
    queryset = EDVisit.objects.all()
    filterset_fields = ["patient", "status"]
    audited_fields = ("chief_complaint", "status")

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)
        self._log("create", serializer.instance)

    @action(detail=True, methods=["post"], url_path="admit-to-ipd")
    def admit_to_ipd(self, request, pk=None):
        ed_visit = self.get_object()
        admitting_doctor_id = request.data.get("admitting_doctor")
        bed_id = request.data.get("bed")

        if not admitting_doctor_id or not bed_id:
            return Response(
                {"error": "admitting_doctor and bed are required to admit from ED."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        admission = Admission.objects.create(
            hospital=ed_visit.hospital,
            patient=ed_visit.patient,
            admitting_doctor_id=admitting_doctor_id,
            bed_id=bed_id,
            source_ed_visit=ed_visit,
            admission_type=Admission.AdmissionType.EMERGENCY,
            status=Admission.Status.ADMITTED,
            admission_diagnosis=f"Admitted via ED: {ed_visit.chief_complaint}",
        )

        ed_visit.status = EDVisit.Status.ADMITTED
        ed_visit.save(update_fields=["status"])

        # Emit domain event
        admission_required.send(sender=self.__class__, ed_visit=ed_visit, admission=admission)

        return Response(AdmissionSerializer(admission).data, status=status.HTTP_201_CREATED)


class TriageViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = TriageSerializer
    queryset = Triage.objects.all()
    filterset_fields = ["ed_visit", "triage_category"]
    audited_fields = ("triage_category", "vitals_summary")

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, triaged_by=self.request.user)
        self._log("create", serializer.instance)
