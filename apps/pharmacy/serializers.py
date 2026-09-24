from rest_framework import serializers

from .models import DispenseRecord, Medicine, MedicineBatch, StockAdjustment, Supplier


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ["id", "name", "contact_person", "phone", "email", "is_active"]
        read_only_fields = ["id"]


class MedicineSerializer(serializers.ModelSerializer):
    total_available = serializers.SerializerMethodField()

    class Meta:
        model = Medicine
        fields = ["id", "name", "generic_name", "form", "unit", "reorder_level", "is_active", "total_available", "strength", "route", "category", "drug_code", "is_formulary", "is_high_risk", "is_lasa", "lasa_pair", "is_emergency", "is_controlled", "is_restricted_antimicrobial", "storage",
        ]
        read_only_fields = ["id", "total_available"]

    def get_total_available(self, obj):
        return sum(b.quantity_available for b in obj.batches.all())


class MedicineBatchSerializer(serializers.ModelSerializer):
    medicine_name = serializers.CharField(source="medicine.name", read_only=True)

    class Meta:
        model = MedicineBatch
        fields = ["id", "medicine", "medicine_name", "batch_number", "expiry_date", "quantity_available", "mrp", "purchase_price", "supplier", "is_quarantined",
        ]
        read_only_fields = ["id", "quantity_available"]


class DispenseRecordSerializer(serializers.ModelSerializer):
    medicine_name = serializers.CharField(source="batch.medicine.name", read_only=True)

    class Meta:
        model = DispenseRecord
        fields = ["id", "prescription", "batch", "medicine_name", "quantity", "dispensed_by", "dispensed_at", "patient", "verified_by", "is_non_formulary", "override_reason",
        ]
        read_only_fields = ["id", "dispensed_by", "dispensed_at", "patient", "verified_by", "is_non_formulary", "override_reason"]


class DispenseRequestSerializer(serializers.Serializer):
    """Input shape for POST /pharmacy/dispense-records/ — stock-decrement
    locking happens in apps.pharmacy.services.dispense_medicine, not here."""

    batch = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)
    prescription = serializers.IntegerField(required=False, allow_null=True)
    patient = serializers.IntegerField(required=False, allow_null=True)
    verified_by = serializers.IntegerField(required=False, allow_null=True)
    override_reason = serializers.CharField(required=False, allow_blank=True)


class StockAdjustmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockAdjustment
        fields = ["id", "batch", "adjustment_type", "quantity_delta", "reason", "adjusted_by", "adjusted_at"]
        read_only_fields = ["id", "adjusted_by", "adjusted_at"]
