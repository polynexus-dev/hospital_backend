from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import ActionPermissionRequired, RequiresClinicalDetailPermission, RoleBasedModelPermissions
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin

from .models import BloodUnit, CrossMatchRequest, Donor, Transfusion
from .workflow import CrossMatchWorkflowMixin, TransfusionWorkflowMixin, stock_summary
from .serializers import BloodUnitSerializer, CrossMatchRequestSerializer, DonorSerializer, TransfusionSerializer

CLINICAL_PERMISSION_CLASSES = [IsAuthenticated, RoleBasedModelPermissions, ActionPermissionRequired, RequiresClinicalDetailPermission]


class DonorViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = DonorSerializer
    queryset = Donor.objects.all()
    filterset_fields = ["blood_group"]
    audited_fields = ("name", "blood_group", "phone")

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)
        self._log("create", serializer.instance)


class BloodUnitViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = BloodUnitSerializer
    queryset = BloodUnit.objects.all()
    filterset_fields = ["blood_group", "component", "status"]
    audited_fields = ("blood_group", "component", "status")

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital)
        self._log("create", serializer.instance)


class CrossMatchRequestViewSet(CrossMatchWorkflowMixin, AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = CrossMatchRequestSerializer
    queryset = CrossMatchRequest.objects.all()
    filterset_fields = ["patient", "status", "blood_group_required"]
    audited_fields = ("blood_group_required", "component", "status")

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, requested_by=self.request.user)
        self._log("create", serializer.instance)


class TransfusionViewSet(TransfusionWorkflowMixin, AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = TransfusionSerializer
    queryset = Transfusion.objects.all()
    filterset_fields = ["patient", "blood_unit", "admission"]
    audited_fields = ("patient", "blood_unit", "reaction_notes")

    def perform_create(self, serializer):
        # COP.3.d safe transfusion: the unit must be usable and match, and
        # the bedside check needs a second person.
        from django.utils import timezone
        from rest_framework.exceptions import ValidationError

        unit = serializer.validated_data["blood_unit"]
        patient = serializer.validated_data["patient"]
        if unit.status in (BloodUnit.Status.ISSUED, BloodUnit.Status.DISCARDED):
            raise ValidationError({"blood_unit": "This unit has already been issued or discarded."})
        if unit.expiry_date < timezone.localdate():
            raise ValidationError({"blood_unit": "This unit is expired."})
        group = (patient.get_blood_group_display() if patient.blood_group else "").replace(" ", "")
        if group and unit.component in ("whole_blood", "prbc") and not _abo_compatible(unit.blood_group, group):
            raise ValidationError({"blood_unit": f"Unit {unit.blood_group} is not compatible with patient group {group}."})
        verifier = serializer.validated_data.get("bedside_verified_by")
        if verifier is not None and verifier.pk == self.request.user.pk:
            raise ValidationError({"bedside_verified_by": "The bedside check must be done by a second person."})
        hospital = getattr(self.request.user, "hospital", None)
        instance = serializer.save(hospital=hospital, issued_by=self.request.user)
        instance.blood_unit.status = BloodUnit.Status.ISSUED
        instance.blood_unit.save(update_fields=["status"])
        self._log("create", instance)


class BloodStockSummaryViewSet(viewsets.ViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES

    def list(self, request):
        return Response(stock_summary(request.user.hospital_id))


_RED_CELL_COMPAT = {
    "O-": {"O-"}, "O+": {"O-", "O+"}, "A-": {"O-", "A-"}, "A+": {"O-", "O+", "A-", "A+"},
    "B-": {"O-", "B-"}, "B+": {"O-", "O+", "B-", "B+"}, "AB-": {"O-", "A-", "B-", "AB-"},
    "AB+": {"O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+"},
}


def _abo_compatible(unit_group, patient_group):
    donors = _RED_CELL_COMPAT.get(patient_group.upper())
    return donors is None or unit_group.upper().replace(" ", "") in donors
