from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import ActionPermissionRequired, RequiresClinicalDetailPermission, RoleBasedModelPermissions
from apps.core.viewsets import TenantScopedViewSetMixin
from apps.patients.models import Prescription

from .models import DispenseRecord, Medicine, MedicineBatch, StockAdjustment, Supplier
from .serializers import (
    DispenseRecordSerializer,
    DispenseRequestSerializer,
    MedicineBatchSerializer,
    MedicineSerializer,
    StockAdjustmentSerializer,
    SupplierSerializer,
)
from .services import InsufficientStock, adjust_stock, dispense_medicine

CLINICAL_PERMISSION_CLASSES = [IsAuthenticated, RoleBasedModelPermissions, ActionPermissionRequired, RequiresClinicalDetailPermission]


class SupplierViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """Not clinical content — no gate beyond the standard RBAC verbs,
    same reasoning as apps.laboratory.LabTestViewSet's catalogue data."""

    serializer_class = SupplierSerializer
    queryset = Supplier.objects.all()
    filterset_fields = ["is_active"]
    search_fields = ["name"]


class MedicineViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = MedicineSerializer
    queryset = Medicine.objects.all()
    filterset_fields = ["form", "is_active"]
    search_fields = ["name", "generic_name"]


class MedicineBatchViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = MedicineBatchSerializer
    queryset = MedicineBatch.objects.all()
    filterset_fields = ["medicine", "supplier"]


class DispenseRecordViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """Dispensing reveals what a patient is being medicated for — gated
    like every other clinical ViewSet, unlike the catalogue ViewSets above."""

    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = DispenseRecordSerializer
    queryset = DispenseRecord.objects.all()
    filterset_fields = ["prescription", "batch"]

    def create(self, request, *args, **kwargs):
        serializer = DispenseRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        hospital = getattr(request.user, "hospital", None)
        batch = get_object_or_404(MedicineBatch, pk=data["batch"], hospital=hospital)
        prescription = get_object_or_404(Prescription, pk=data["prescription"], hospital=hospital) if data.get("prescription") else None

        try:
            record = dispense_medicine(
                hospital=hospital, batch=batch, quantity=data["quantity"], dispensed_by=request.user, prescription=prescription,
            )
        except InsufficientStock as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(DispenseRecordSerializer(record).data, status=status.HTTP_201_CREATED)


class StockAdjustmentViewSet(TenantScopedViewSetMixin, viewsets.ModelViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = StockAdjustmentSerializer
    queryset = StockAdjustment.objects.all()
    filterset_fields = ["batch", "adjustment_type"]

    def create(self, request, *args, **kwargs):
        serializer = StockAdjustmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        hospital = getattr(request.user, "hospital", None)
        batch = get_object_or_404(MedicineBatch, pk=data["batch"].pk, hospital=hospital)

        try:
            adjustment = adjust_stock(
                hospital=hospital, batch=batch, adjustment_type=data["adjustment_type"],
                quantity_delta=data["quantity_delta"], reason=data.get("reason", ""), adjusted_by=request.user,
            )
        except InsufficientStock as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(StockAdjustmentSerializer(adjustment).data, status=status.HTTP_201_CREATED)
