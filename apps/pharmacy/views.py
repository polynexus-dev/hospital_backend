from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import ActionPermissionRequired, RequiresClinicalDetailPermission, RoleBasedModelPermissions
from apps.core.viewsets import TenantScopedViewSetMixin, AuditedModelViewSetMixin
from apps.patients.models import Prescription

from .models import DispenseRecord, Medicine, MedicineBatch, StockAdjustment, Supplier
from .workflow import BatchWorkflowMixin, MedicineWorkflowMixin, pre_dispense_checks
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


class MedicineViewSet(MedicineWorkflowMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = MedicineSerializer
    # prefetch_related: MedicineSerializer.get_total_available iterates
    # obj.batches.all() per row.
    queryset = Medicine.objects.prefetch_related("batches")
    filterset_fields = ["form", "is_active"]
    search_fields = ["name", "generic_name"]


class MedicineBatchViewSet(BatchWorkflowMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = MedicineBatchSerializer
    # select_related: MedicineBatchSerializer.medicine_name (source="medicine.name")
    queryset = MedicineBatch.objects.select_related("medicine")
    filterset_fields = ["medicine", "supplier"]


class DispenseRecordViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """Dispensing reveals what a patient is being medicated for — gated
    like every other clinical ViewSet, unlike the catalogue ViewSets above."""

    permission_classes = CLINICAL_PERMISSION_CLASSES
    serializer_class = DispenseRecordSerializer
    # select_related: DispenseRecordSerializer.medicine_name (source="batch.medicine.name")
    queryset = DispenseRecord.objects.select_related("batch__medicine", "bill_item__bill")
    filterset_fields = ["prescription", "batch", "patient"]

    def get_queryset(self):
        qs = super().get_queryset()
        billed = self.request.query_params.get("billed")
        if billed in ("true", "false"):
            qs = qs.filter(bill_item__isnull=billed == "false")
        return qs

    def create(self, request, *args, **kwargs):
        serializer = DispenseRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        hospital = getattr(request.user, "hospital", None)
        batch = get_object_or_404(MedicineBatch, pk=data["batch"], hospital=hospital)
        prescription = get_object_or_404(Prescription, pk=data["prescription"], hospital=hospital) if data.get("prescription") else None
        from apps.patients.models import Patient

        patient = prescription.patient if prescription else (Patient.objects.filter(pk=data.get("patient"), hospital=hospital).first() if data.get("patient") else None)
        extra, error = pre_dispense_checks(request, batch, patient)  # NABH MOM.2.c/f/g
        if error is not None:
            return error

        try:
            record = dispense_medicine(
                hospital=hospital, batch=batch, quantity=data["quantity"], dispensed_by=request.user, prescription=prescription,
            )
        except InsufficientStock as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        for field, value in extra.items():
            setattr(record, field, value)
        record.save(update_fields=list(extra))
        from .billing import bill_dispense, mark_unbilled

        if data.get("bill", True):
            bill_dispense(record)
        else:
            mark_unbilled(record, f"Issued without charge: {data['no_charge_reason'].strip()}")
        return Response(DispenseRecordSerializer(record).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def bill(self, request, pk=None):
        """Charge an unbilled dispense now (e.g. after the bill's hold is released)."""
        from .billing import bill_dispense

        record = self.get_object()
        if bill_dispense(record) is None:
            return Response({"detail": record.billing_note}, status=status.HTTP_400_BAD_REQUEST)
        return Response(DispenseRecordSerializer(record).data)


class StockAdjustmentViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
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
