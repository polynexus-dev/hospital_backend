from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.crud import TenantCRUDViewSet, TenantModelSerializer, model_serializer

from .models_laundry import THERMAL_MIN_MINUTES, THERMAL_MIN_TEMP_C, LaundryBatch, LaundryBatchLine, LinenStock, LinenType


class LinenTypeViewSet(TenantCRUDViewSet):
    serializer_class = model_serializer(LinenType)
    queryset = LinenType.objects.all()
    filterset_fields = ["category", "is_active"]


class LinenStockSerializer(TenantModelSerializer):
    ward_name = serializers.CharField(source="ward.name", read_only=True, default="Central linen store")
    linen_name = serializers.CharField(source="linen_type.name", read_only=True)
    shortfall = serializers.IntegerField(read_only=True)

    class Meta:
        model = LinenStock
        fields = "__all__"


class LinenStockViewSet(TenantCRUDViewSet):
    """Clean stock per ward (blank ward = central store), with par levels."""

    serializer_class = LinenStockSerializer
    queryset = LinenStock.objects.select_related("ward", "linen_type")
    filterset_fields = ["ward", "linen_type"]
    audited_fields = ("par_level", "clean_qty")

    @action(detail=False, methods=["post"])
    @transaction.atomic
    def issue(self, request):
        """POST {linen_type, ward, quantity} — move clean linen from the central store to a ward."""
        try:
            qty = int(request.data.get("quantity", 0))
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            return Response({"quantity": "A positive number."}, status=status.HTTP_400_BAD_REQUEST)
        h = request.user.hospital_id
        central = LinenStock.objects.select_for_update().filter(hospital_id=h, ward__isnull=True, linen_type_id=request.data.get("linen_type")).first()
        if central is None or central.clean_qty < qty:
            return Response({"quantity": f"Central store has {central.clean_qty if central else 0} clean."}, status=status.HTTP_400_BAD_REQUEST)
        ward_stock, _ = LinenStock.objects.get_or_create(hospital_id=h, ward_id=request.data.get("ward"), linen_type_id=central.linen_type_id)
        LinenStock.objects.filter(pk=central.pk).update(clean_qty=F("clean_qty") - qty)
        LinenStock.objects.filter(pk=ward_stock.pk).update(clean_qty=F("clean_qty") + qty)
        return Response({"issued": qty, "ward_clean_qty": ward_stock.clean_qty + qty})


class BatchLineSerializer(serializers.ModelSerializer):
    linen_name = serializers.CharField(source="linen_type.name", read_only=True)
    lost_qty = serializers.IntegerField(read_only=True)

    class Meta:
        model = LaundryBatchLine
        fields = ["id", "linen_type", "linen_name", "sent_qty", "returned_qty", "rejected_qty", "lost_qty"]


class LaundryBatchSerializer(TenantModelSerializer):
    lines = BatchLineSerializer(many=True)
    ward_name = serializers.CharField(source="ward.name", read_only=True, default="")
    cost = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = LaundryBatch
        fields = "__all__"
        read_only_fields = ["batch_number", "status", "collected_by", "processed_at", "returned_at", "returned_by",
                            "wash_temp_c", "wash_minutes", "disinfectant"]

    def validate_lines(self, lines):
        if not lines:
            raise serializers.ValidationError("Count at least one linen type.")
        if len({ln["linen_type"].pk for ln in lines}) != len(lines):
            raise serializers.ValidationError("Each linen type once per batch.")
        for ln in lines:
            if ln["linen_type"].hospital_id != self.context["request"].user.hospital_id:
                raise serializers.ValidationError("Unknown linen type.")
        return lines

    def create(self, validated):
        lines = validated.pop("lines")
        batch = LaundryBatch.objects.create(**validated)
        LaundryBatchLine.objects.bulk_create([LaundryBatchLine(batch=batch, linen_type=ln["linen_type"], sent_qty=ln["sent_qty"]) for ln in lines])
        return batch

    def update(self, instance, validated):
        validated.pop("lines", None)  # counts change through the process/return actions only
        return super().update(instance, validated)


class LaundryBatchViewSet(TenantCRUDViewSet):
    """Collect → wash → return. POST {ward, kind, vendor?, rate_per_kg?, weight_kg?, lines: [{linen_type, sent_qty}]}"""

    serializer_class = LaundryBatchSerializer
    queryset = LaundryBatch.objects.select_related("ward").prefetch_related("lines__linen_type")
    filterset_fields = ["ward", "kind", "status"]
    actor_field = "collected_by"
    audited_fields = ("status", "wash_temp_c", "disinfectant")

    @action(detail=True, methods=["post"])
    def process(self, request, pk=None):
        """POST {wash_temp_c, wash_minutes, disinfectant?} — infected linen needs a validated wash."""
        batch = self.get_object()
        if batch.status != LaundryBatch.Status.COLLECTED:
            return Response({"status": "Already washed."}, status=status.HTTP_400_BAD_REQUEST)
        temp = request.data.get("wash_temp_c")
        minutes = request.data.get("wash_minutes")
        disinfectant = (request.data.get("disinfectant") or "").strip()
        try:
            temp = int(temp) if temp not in (None, "") else None
            minutes = int(minutes) if minutes not in (None, "") else None
        except (TypeError, ValueError):
            return Response({"wash_temp_c": "Numbers only."}, status=status.HTTP_400_BAD_REQUEST)
        thermal_ok = temp is not None and minutes is not None and temp >= THERMAL_MIN_TEMP_C and minutes >= THERMAL_MIN_MINUTES
        if batch.kind == LaundryBatch.Kind.INFECTED and not thermal_ok and not disinfectant:
            return Response({"detail": f"Infected linen needs a thermal wash of at least {THERMAL_MIN_TEMP_C} °C for {THERMAL_MIN_MINUTES} min, "
                                       "or a recorded chemical disinfection."}, status=status.HTTP_400_BAD_REQUEST)
        batch.wash_temp_c, batch.wash_minutes, batch.disinfectant = temp, minutes, disinfectant
        batch.status, batch.processed_at = LaundryBatch.Status.PROCESSED, timezone.now()
        batch.save()
        self._log("update", batch)
        return Response(self.get_serializer(batch).data)

    @action(detail=True, methods=["post"], url_path="return")
    @transaction.atomic
    def return_batch(self, request, pk=None):
        """POST {lines: [{linen_type, returned_qty, rejected_qty}], weight_kg?} — clean pieces go back to the ward's stock."""
        batch = self.get_object()
        if batch.status != LaundryBatch.Status.PROCESSED:
            return Response({"status": "Wash the batch before returning it."}, status=status.HTTP_400_BAD_REQUEST)
        counts = {str(r.get("linen_type")): r for r in request.data.get("lines", [])}
        errors = []
        for line in batch.lines.select_related("linen_type"):
            r = counts.get(str(line.linen_type_id), {})
            try:
                returned, rejected = int(r.get("returned_qty", 0) or 0), int(r.get("rejected_qty", 0) or 0)
            except (TypeError, ValueError):
                errors.append(f"{line.linen_type.name}: numbers only")
                continue
            if returned < 0 or rejected < 0 or returned + rejected > line.sent_qty:
                errors.append(f"{line.linen_type.name}: returned + rejected can't exceed {line.sent_qty} sent")
                continue
            line.returned_qty, line.rejected_qty = returned, rejected
            line.save(update_fields=["returned_qty", "rejected_qty"])
            stock, _ = LinenStock.objects.get_or_create(hospital_id=batch.hospital_id, ward=batch.ward, linen_type=line.linen_type)
            LinenStock.objects.filter(pk=stock.pk).update(clean_qty=F("clean_qty") + returned)
        if errors:
            transaction.set_rollback(True)
            return Response({"lines": errors}, status=status.HTTP_400_BAD_REQUEST)
        if request.data.get("weight_kg") not in (None, ""):
            batch.weight_kg = Decimal(str(request.data["weight_kg"]))
        batch.status, batch.returned_at, batch.returned_by = LaundryBatch.Status.RETURNED, timezone.now(), request.user
        batch.save()
        self._log("update", batch)
        return Response(self.get_serializer(batch).data)

    @action(detail=False, methods=["get"])
    def dashboard(self, request):
        """Par-level shortfalls, overdue batches, 30-day losses & condemnations, infected-wash compliance, cost."""
        h = request.user.hospital_id
        since = timezone.now() - timedelta(days=30)
        batches = LaundryBatch.objects.filter(hospital_id=h, collected_at__gte=since).prefetch_related("lines__linen_type")
        losses, returned = {}, [b for b in batches if b.status == LaundryBatch.Status.RETURNED]
        for b in returned:
            for ln in b.lines.all():
                t = losses.setdefault(ln.linen_type.name, {"sent": 0, "lost": 0, "condemned": 0, "value_lost": Decimal("0")})
                t["sent"] += ln.sent_qty
                t["lost"] += ln.lost_qty
                t["condemned"] += ln.rejected_qty
                t["value_lost"] += (ln.lost_qty + ln.rejected_qty) * ln.linen_type.unit_cost
        infected = [b for b in batches if b.kind == LaundryBatch.Kind.INFECTED and b.processed_at]
        tat = [(b.returned_at - b.collected_at).total_seconds() / 3600 for b in returned if b.returned_at]
        overdue_cutoff = timezone.now() - timedelta(hours=24)
        stock = LinenStock.objects.filter(hospital_id=h).select_related("ward", "linen_type")
        return Response({
            "shortfalls": [{"location": s.ward.name if s.ward else "Central linen store", "linen": s.linen_type.name, "par": s.par_level,
                            "clean": s.clean_qty, "short_by": s.shortfall} for s in stock if s.shortfall],
            "overdue_batches": [{"id": b.pk, "batch": b.batch_number, "ward": b.ward.name if b.ward else "", "status": b.status,
                                 "hours": round((timezone.now() - b.collected_at).total_seconds() / 3600, 1)}
                                for b in LaundryBatch.objects.filter(hospital_id=h, collected_at__lt=overdue_cutoff).exclude(status=LaundryBatch.Status.RETURNED).select_related("ward")],
            "losses_30d": losses,
            "average_turnaround_hours": round(sum(tat) / len(tat), 1) if tat else None,
            "infected_batches_30d": len(infected),
            "infected_thermal_or_chemical_compliance_percent": round(100 * sum(
                1 for b in infected
                if (b.wash_temp_c or 0) >= THERMAL_MIN_TEMP_C and (b.wash_minutes or 0) >= THERMAL_MIN_MINUTES or b.disinfectant
            ) / len(infected), 1) if infected else None,
            "laundry_cost_30d": sum((b.cost for b in batches), Decimal("0")),
        })
