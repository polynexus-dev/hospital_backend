from rest_framework import serializers

from .models import RadiologyOrder, RadiologyProcedure, RadiologyReport


class RadiologyProcedureSerializer(serializers.ModelSerializer):
    class Meta:
        model = RadiologyProcedure
        fields = ["id", "name", "modality", "price", "is_active", "code", "uses_contrast", "preparation_instructions", "duration_minutes",
        ]
        read_only_fields = ["id"]


class RadiologyOrderSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    procedure_name = serializers.CharField(source="procedure.name", read_only=True)

    class Meta:
        model = RadiologyOrder
        fields = ["id", "investigation_order", "patient", "patient_name", "procedure", "procedure_name", "status", "ordered_by", "ordered_at", "priority", "clinical_history", "contraindication_override_reason", "is_outsourced", "outsourced_center", "outsourced_report_file", "study_instance_uid", "accession_number", "status_history", "patient_notified_at",
        ]
        read_only_fields = ["id", "status", "ordered_by", "ordered_at", "accession_number", "status_history", "patient_notified_at"]


class RadiologyReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = RadiologyReport
        fields = [
            "id", "radiology_order", "findings", "impression", "reported_by",
            "image_file", "finalized_at", "finalized_by", "created_at", "template", "addendum", "is_amended",
        ]
        read_only_fields = ["id", "reported_by", "finalized_at", "finalized_by", "created_at", "is_amended"]
