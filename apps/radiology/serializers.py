from rest_framework import serializers

from .models import RadiologyOrder, RadiologyProcedure, RadiologyReport


class RadiologyProcedureSerializer(serializers.ModelSerializer):
    class Meta:
        model = RadiologyProcedure
        fields = ["id", "name", "modality", "price", "is_active"]
        read_only_fields = ["id"]


class RadiologyOrderSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    procedure_name = serializers.CharField(source="procedure.name", read_only=True)

    class Meta:
        model = RadiologyOrder
        fields = ["id", "investigation_order", "patient", "patient_name", "procedure", "procedure_name", "status", "ordered_by", "ordered_at"]
        read_only_fields = ["id", "status", "ordered_by", "ordered_at"]


class RadiologyReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = RadiologyReport
        fields = [
            "id", "radiology_order", "findings", "impression", "reported_by",
            "image_file", "finalized_at", "finalized_by", "created_at",
        ]
        read_only_fields = ["id", "reported_by", "finalized_at", "finalized_by", "created_at"]
