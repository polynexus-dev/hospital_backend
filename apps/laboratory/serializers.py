from rest_framework import serializers

from .models import LabOrder, LabResult, LabTest, LabTestPackage, SampleCollection


class LabTestSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabTest
        fields = ["id", "name", "code", "department", "reference_range", "unit", "price", "is_active", "loinc_code", "sample_type", "container", "ref_low", "ref_high", "critical_low", "critical_high", "tat_hours", "method", "report_template", "is_outsourced",
        ]
        read_only_fields = ["id"]


class LabTestPackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = LabTestPackage
        fields = ["id", "name", "tests", "price", "is_active"]
        read_only_fields = ["id"]


class LabOrderSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)

    class Meta:
        model = LabOrder
        fields = ["id", "investigation_order", "patient", "patient_name", "ordered_tests", "status", "ordered_by", "ordered_at", "priority", "clinical_notes", "admission", "order_number", "patient_notified_at",
        ]
        read_only_fields = ["id", "status", "ordered_by", "ordered_at", "order_number", "patient_notified_at"]


class SampleCollectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = SampleCollection
        fields = ["id", "lab_order", "sample_type", "barcode", "collected_by", "collected_at", "rejection_notes", "status", "received_at", "received_by", "rejection_reason", "rejected_at",
        ]
        read_only_fields = ["id", "collected_by", "collected_at", "status", "received_at", "received_by", "rejection_reason", "rejected_at"]


class LabResultSerializer(serializers.ModelSerializer):
    lab_test_name = serializers.CharField(source="lab_test.name", read_only=True)

    class Meta:
        model = LabResult
        fields = [
            "id", "lab_order", "lab_test", "lab_test_name", "value", "unit", "reference_range", "flag",
            "entered_by", "finalized_at", "finalized_by", "created_at", "repeated_from", "interpretation", "needs_repeat", "repeat_reason", "is_amended", "source",
        ]
        read_only_fields = ["id", "entered_by", "finalized_at", "finalized_by", "created_at", "needs_repeat", "repeat_reason", "is_amended", "source"]
