from rest_framework import serializers

from apps.appointments.serializers import DoctorSerializer
from apps.patients.serializers import PatientSerializer
from .models import (
    AnaesthesiaRecord,
    ConsumableUsage,
    ImplantUsage,
    OperativeNote,
    OTSchedule,
    PreOpChecklist,
    SurgeryRequest,
)


class PreOpChecklistSerializer(serializers.ModelSerializer):
    class Meta:
        model = PreOpChecklist
        fields = [
            "id",
            "surgery_request",
            "consent_obtained",
            "fasting_confirmed",
            "site_marked",
            "completed_by",
            "completed_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ConsumableUsageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsumableUsage
        fields = ["id", "ot_schedule", "item_name", "quantity", "created_at"]
        read_only_fields = ["id", "created_at"]


class ImplantUsageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ImplantUsage
        fields = ["id", "ot_schedule", "implant_name", "serial_number", "quantity", "created_at"]
        read_only_fields = ["id", "created_at"]


class OperativeNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = OperativeNote
        fields = [
            "id",
            "ot_schedule",
            "procedure_performed",
            "findings",
            "surgeon",
            "started_at",
            "ended_at",
            "finalized_at",
            "finalized_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "finalized_at", "finalized_by", "created_at", "updated_at"]


class AnaesthesiaRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnaesthesiaRecord
        fields = [
            "id",
            "ot_schedule",
            "anaesthesia_type",
            "intra_op_notes",
            "anaesthetist",
            "finalized_at",
            "finalized_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "finalized_at", "finalized_by", "created_at", "updated_at"]


class OTScheduleSerializer(serializers.ModelSerializer):
    surgeon_detail = DoctorSerializer(source="surgeon", read_only=True)
    operative_note = OperativeNoteSerializer(read_only=True)
    anaesthesia_record = AnaesthesiaRecordSerializer(read_only=True)
    consumable_usages = ConsumableUsageSerializer(many=True, read_only=True)
    implant_usages = ImplantUsageSerializer(many=True, read_only=True)

    class Meta:
        model = OTSchedule
        fields = [
            "id",
            "surgery_request",
            "operation_theatre_room",
            "surgeon",
            "surgeon_detail",
            "anaesthetist",
            "scheduled_start",
            "scheduled_end",
            "operative_note",
            "anaesthesia_record",
            "consumable_usages",
            "implant_usages",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class SurgeryRequestSerializer(serializers.ModelSerializer):
    patient_detail = PatientSerializer(source="patient", read_only=True)
    schedule = OTScheduleSerializer(read_only=True)
    preop_checklist = PreOpChecklistSerializer(read_only=True)

    class Meta:
        model = SurgeryRequest
        fields = [
            "id",
            "patient",
            "patient_detail",
            "admission",
            "requested_by",
            "proposed_procedure",
            "status",
            "schedule",
            "preop_checklist",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
