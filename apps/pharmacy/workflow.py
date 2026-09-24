"""NABH MOM chapter additions to pharmacy: safe dispensing checks, stock
alerts, formulary lookup, returns/recalls, reconciliation, indents,
emergency medication stock."""
from datetime import timedelta

from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.core.crud import ClinicalCRUDViewSet, TenantCRUDViewSet, model_serializer

from .models import (
    DispenseRecord,
    EmergencyMedicationStock,
    Medicine,
    MedicineBatch,
    MedicineRecall,
    MedicineReturn,
    MedicationReconciliation,
    PharmacyIndent,
    StockOutEvent,
)


def pre_dispense_checks(request, batch, patient):
    """Returns (extra_fields, error_response_or_None)."""
    med = batch.medicine
    if batch.is_quarantined:
        return None, Response({"detail": "Batch is quarantined (recall/hold) and cannot be dispensed."}, status=409)
    if batch.expiry_date < timezone.localdate():
        return None, Response({"detail": "Batch has expired.", "code": "expired"}, status=409)
    override = str(request.data.get("override_reason", "")).strip()
    extra = {"patient": patient, "is_non_formulary": not med.is_formulary, "override_reason": override[:255]}
    if patient is not None:
        from apps.clinical.services import check_medications

        alerts = [a for a in check_medications(patient, [{"name": med.name, "generic_name": med.generic_name}]) if a["alert_type"] in ("allergy", "interaction", "contraindication") and a["severity"] == "critical"]
        if alerts and not override:
            return None, Response({"detail": "Safety alert — an override reason is required to dispense.", "alerts": alerts, "code": "safety_alert"}, status=409)
    if med.is_high_risk:
        from apps.accounts.models import User

        verifier = User.objects.filter(pk=request.data.get("verified_by"), hospital_id=request.user.hospital_id).first()
        if verifier is None or verifier.pk == request.user.pk:
            return None, Response({"verified_by": "High-risk medication — a second pharmacist/nurse must independently verify before dispensing.", "code": "double_check_required"}, status=400)
        extra["verified_by"] = verifier
    return extra, None


class MedicineWorkflowMixin:
    @action(detail=False, methods=["get"])
    def reorder_alerts(self, request):
        """MOM.1.c"""
        qs = self.get_queryset().filter(is_active=True).annotate(
            stock=Sum("batches__quantity_available", filter=Q(batches__is_quarantined=False, batches__expiry_date__gte=timezone.localdate())),
        )
        rows = [{"id": m.pk, "name": m.name, "stock": m.stock or 0, "reorder_level": m.reorder_level, "is_emergency": m.is_emergency}
                for m in qs if (m.stock or 0) <= m.reorder_level]
        return Response(rows)

    @action(detail=False, methods=["get"])
    def formulary(self, request):
        """MOM.2.e — prescriber search restricted to (or ranking) the
        hospital formulary; non-formulary items come back flagged."""
        q = request.query_params.get("q", "").strip()
        qs = self.get_queryset().filter(is_active=True)
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(generic_name__icontains=q))
        qs = qs.order_by("-is_formulary", "name")[:25]
        return Response([{
            "id": m.pk, "name": m.name, "generic_name": m.generic_name, "strength": m.strength, "form": m.form, "route": m.route,
            "is_formulary": m.is_formulary, "is_high_risk": m.is_high_risk, "is_lasa": m.is_lasa, "lasa_pair": m.lasa_pair,
            "is_restricted_antimicrobial": m.is_restricted_antimicrobial,
        } for m in qs])

    @action(detail=False, methods=["get"])
    def stock_report(self, request):
        """MOM.2.d — stock on hand & value per medicine."""
        rows = []
        for m in self.get_queryset().filter(is_active=True).prefetch_related("batches"):
            live = [b for b in m.batches.all() if not b.is_quarantined and b.expiry_date >= timezone.localdate()]
            qty = sum(b.quantity_available for b in live)
            rows.append({"id": m.pk, "name": m.name, "quantity": qty, "value": float(sum(b.quantity_available * b.purchase_price for b in live)),
                         "batches": len(live), "nearest_expiry": min((b.expiry_date for b in live), default=None), "below_reorder": qty <= m.reorder_level})
        return Response(rows)


class BatchWorkflowMixin:
    @action(detail=False, methods=["get"])
    def expiring(self, request):
        """MOM.2.i — batches expiring within ?days (default 90)."""
        horizon = timezone.localdate() + timedelta(days=int(request.query_params.get("days", 90)))
        qs = self.get_queryset().filter(expiry_date__lte=horizon, quantity_available__gt=0).order_by("expiry_date")
        return Response([{"id": b.pk, "medicine": b.medicine.name, "batch_number": b.batch_number, "expiry_date": b.expiry_date,
                          "quantity": b.quantity_available, "expired": b.expiry_date < timezone.localdate()} for b in qs])


class MedicineReturnViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(MedicineReturn, read_only=("returned_by",), extra={"medicine_name": serializers.CharField(source="batch.medicine.name", read_only=True)})
    queryset = MedicineReturn.objects.select_related("batch__medicine")
    filterset_fields = ["batch", "patient", "restocked"]
    actor_field = "returned_by"

    def perform_create(self, serializer):
        with transaction.atomic():
            super().perform_create(serializer)
            r = serializer.instance
            if r.restocked:
                MedicineBatch.objects.filter(pk=r.batch_id).update(quantity_available=F("quantity_available") + r.quantity)


class MedicineRecallViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(MedicineRecall, read_only=("initiated_by", "closed_at"), extra={"medicine_name": serializers.CharField(source="medicine.name", read_only=True)})
    queryset = MedicineRecall.objects.select_related("medicine").prefetch_related("batches")
    filterset_fields = ["status", "medicine"]
    actor_field = "initiated_by"

    def perform_create(self, serializer):
        super().perform_create(serializer)
        recall = serializer.instance
        if not recall.batches.exists():
            recall.batches.set(MedicineBatch.objects.filter(medicine=recall.medicine))
        recall.batches.update(is_quarantined=True)

    @action(detail=True, methods=["get"])
    def affected_patients(self, request, pk=None):
        recall = self.get_object()
        rows = DispenseRecord.objects.filter(batch__in=recall.batches.all(), patient__isnull=False).values(
            "patient_id", "patient__first_name", "patient__last_name", "patient__uhid", "batch__batch_number", "dispensed_at",
        )
        return Response(list(rows))

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        recall = self.get_object()
        recall.status = MedicineRecall.Status.CLOSED
        recall.closed_at = timezone.now()
        recall.save(update_fields=["status", "closed_at"])
        return Response(self.get_serializer(recall).data)


class MedicationReconciliationViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(MedicationReconciliation, read_only=("reconciled_by", "discrepancies_found"), extra={
        "patient_name": serializers.CharField(source="patient.full_name", read_only=True),
    })
    queryset = MedicationReconciliation.objects.select_related("patient")
    filterset_fields = ["patient", "admission", "stage"]
    actor_field = "reconciled_by"

    def perform_create(self, serializer):
        for item in serializer.validated_data.get("items", []):
            if item.get("decision") not in ("continue", "stop", "modify", "new"):
                raise ValidationError({"items": "Every medication needs a decision: continue, stop, modify or new."})
        super().perform_create(serializer)


class PharmacyIndentViewSet(ClinicalCRUDViewSet):
    serializer_class = model_serializer(PharmacyIndent, read_only=("indent_number", "status", "requested_by", "issued_by", "issued_at"))
    queryset = PharmacyIndent.objects.all()
    filterset_fields = ["status", "department", "admission", "patient"]
    actor_field = "requested_by"

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        """Issues each line first-expiry-first-out from live batches."""
        from .services import InsufficientStock, dispense_medicine

        indent = self.get_object()
        if indent.status in (PharmacyIndent.Status.ISSUED, PharmacyIndent.Status.REJECTED):
            return Response({"detail": f"Indent is {indent.status}."}, status=400)
        items = []
        all_done = True
        with transaction.atomic():
            for line in indent.items:
                need = int(line.get("quantity", 0)) - int(line.get("issued_quantity", 0))
                batches = MedicineBatch.objects.filter(hospital_id=indent.hospital_id, medicine_id=line.get("medicine"), is_quarantined=False,
                                                       expiry_date__gte=timezone.localdate(), quantity_available__gt=0).order_by("expiry_date")
                for b in batches:
                    if need <= 0:
                        break
                    take = min(need, b.quantity_available)
                    try:
                        dispense_medicine(hospital=indent.hospital, batch=b, quantity=take, dispensed_by=request.user)
                    except InsufficientStock:
                        continue
                    need -= take
                    line["issued_quantity"] = int(line.get("issued_quantity", 0)) + take
                all_done &= need <= 0
                items.append(line)
            indent.items = items
            indent.status = PharmacyIndent.Status.ISSUED if all_done else PharmacyIndent.Status.PARTIAL
            indent.issued_by = request.user
            indent.issued_at = timezone.now()
            indent.save()
        return Response(self.get_serializer(indent).data)


class EmergencyMedicationStockViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(EmergencyMedicationStock, read_only=("last_checked_at", "last_checked_by"), extra={
        "medicine_name": serializers.CharField(source="medicine.name", read_only=True),
    })
    queryset = EmergencyMedicationStock.objects.select_related("medicine")
    filterset_fields = ["location", "medicine"]
    audited_fields = ("current_quantity", "par_level", "expiry_date")

    @action(detail=True, methods=["post"])
    def check(self, request, pk=None):
        s = self.get_object()
        if "current_quantity" in request.data:
            s.current_quantity = int(request.data["current_quantity"])
        if request.data.get("expiry_date"):
            s.expiry_date = request.data["expiry_date"]
        s.last_checked_at = timezone.now()
        s.last_checked_by = request.user
        s.save()
        return Response(self.get_serializer(s).data)

    @action(detail=False, methods=["get"])
    def shortfalls(self, request):
        soon = timezone.localdate() + timedelta(days=30)
        qs = self.get_queryset().filter(Q(current_quantity__lt=F("par_level")) | Q(expiry_date__lte=soon))
        return Response(self.get_serializer(qs, many=True).data)


class StockOutEventViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(StockOutEvent, extra={"medicine_name": serializers.CharField(source="medicine.name", read_only=True)})
    queryset = StockOutEvent.objects.select_related("medicine")
    filterset_fields = ["is_emergency_medication", "medicine"]
