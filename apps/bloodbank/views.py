from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import ActionPermissionRequired, RequiresClinicalDetailPermission, RoleBasedModelPermissions
from apps.core.viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin

from .models import BloodUnit, CrossMatchRequest, Donor, Transfusion
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


class CrossMatchRequestViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = CrossMatchRequestSerializer
    queryset = CrossMatchRequest.objects.all()
    filterset_fields = ["patient", "status", "blood_group_required"]
    audited_fields = ("blood_group_required", "component", "status")

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        serializer.save(hospital=hospital, requested_by=self.request.user)
        self._log("create", serializer.instance)


class TransfusionViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = TransfusionSerializer
    queryset = Transfusion.objects.all()
    filterset_fields = ["patient", "blood_unit", "admission"]
    audited_fields = ("patient", "blood_unit", "reaction_notes")

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        instance = serializer.save(hospital=hospital, issued_by=self.request.user)
        instance.blood_unit.status = BloodUnit.Status.ISSUED
        instance.blood_unit.save(update_fields=["status"])
        self._log("create", instance)
