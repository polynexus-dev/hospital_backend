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


class StampedSerializerMixin(serializers.Serializer):
    """Records who last changed the record (updated_by) and shows it with
    updated_at, so every OT screen can say "Updated by … on …"."""

    updated_by_name = serializers.SerializerMethodField()

    def get_updated_by_name(self, obj):
        u = getattr(obj, "updated_by", None)
        return (u.get_full_name() or u.email) if u else ""

    def save(self, **kwargs):
        request = self.context.get("request")
        if request is not None and getattr(request.user, "is_authenticated", False):
            kwargs.setdefault("updated_by", request.user)
        return super().save(**kwargs)


class PreOpChecklistSerializer(StampedSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = PreOpChecklist
        fields = ["updated_by", "updated_by_name", 
            "id",
            "surgery_request",
            "consent_obtained",
            "fasting_confirmed",
            "site_marked",
            "completed_by",
            "completed_at",
            "created_at",
            "updated_at", "asa_grade", "airway_assessment", "comorbidities", "investigations_reviewed", "blood_arranged", "npo_since", "premedication", "fit_for_surgery", "assessment_notes",
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
        fields = ["id", "ot_schedule", "implant_name", "serial_number", "quantity", "created_at", "manufacturer", "batch_number", "expiry_date", "implanted_site", "sticker_image",
        ]
        read_only_fields = ["id", "created_at"]


class OperativeNoteSerializer(StampedSerializerMixin, serializers.ModelSerializer):
    class Meta:
        model = OperativeNote
        fields = ["updated_by", "updated_by_name", 
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


def _rows(value, name):
    if not isinstance(value, list) or not all(isinstance(r, dict) for r in value):
        raise serializers.ValidationError(f"{name} must be a list of rows.")
    return value


class AnaesthesiaRecordSerializer(StampedSerializerMixin, serializers.ModelSerializer):
    def validate_vitals(self, value):
        return _rows(value, "Vitals")

    def validate_drugs(self, value):
        return _rows(value, "Drugs")

    def validate_fluids(self, value):
        return _rows(value, "Fluids")

    class Meta:
        model = AnaesthesiaRecord
        fields = ["updated_by", "updated_by_name", 
            "id",
            "ot_schedule",
            "anaesthesia_type",
            "intra_op_notes",
            "anaesthetist",
            "finalized_at",
            "finalized_by",
            "created_at",
            "updated_at", "asa_grade", "airway_device", "induction_at", "reversal_at", "drugs", "vitals", "fluids", "estimated_blood_loss_ml", "urine_output_ml", "complications", "recovery_aldrete_score", "shifted_to",
        ]
        read_only_fields = ["id", "finalized_at", "finalized_by", "created_at", "updated_at"]


class OTScheduleSerializer(StampedSerializerMixin, serializers.ModelSerializer):
    surgeon_detail = DoctorSerializer(source="surgeon", read_only=True)
    assistant_surgeon_name = serializers.CharField(source="assistant_surgeon.name", read_only=True, default="")
    operative_note = OperativeNoteSerializer(read_only=True)
    anaesthesia_record = AnaesthesiaRecordSerializer(read_only=True)
    consumable_usages = ConsumableUsageSerializer(many=True, read_only=True)
    implant_usages = ImplantUsageSerializer(many=True, read_only=True)

    class Meta:
        model = OTSchedule
        fields = ["updated_by", "updated_by_name", 
            "id",
            "surgery_request",
            "operation_theatre_room",
            "surgeon",
            "surgeon_detail",
            "assistant_surgeon",
            "assistant_surgeon_name",
            "anaesthetist",
            "scheduled_start",
            "scheduled_end",
            "operative_note",
            "anaesthesia_record",
            "consumable_usages",
            "implant_usages",
            "created_at",
            "updated_at", "status", "last_reschedule_reason", "cancellation_reason", "actual_start", "actual_end", "wheeled_in_at", "wheeled_out_at", "reschedule_count", "cancelled_at", "charges_posted_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "reschedule_count", "cancelled_at", "charges_posted_at"]


class SurgeryRequestSerializer(StampedSerializerMixin, serializers.ModelSerializer):
    patient_detail = PatientSerializer(source="patient", read_only=True)
    schedule = OTScheduleSerializer(read_only=True)
    preop_checklist = PreOpChecklistSerializer(read_only=True)
    surgery_grade_name = serializers.CharField(source="surgery_grade.name", read_only=True, default="")
    encounter = serializers.CharField(read_only=True)

    def validate_surgery_grade(self, value):
        user = self.context["request"].user
        if value is not None and value.hospital_id != getattr(user, "hospital_id", None):
            raise serializers.ValidationError("Refers to a record from a different hospital.")
        if value is not None and not value.is_active and getattr(self.instance, "surgery_grade_id", None) != value.pk:
            raise serializers.ValidationError("That surgery grade is inactive.")
        return value

    class Meta:
        model = SurgeryRequest
        fields = ["updated_by", "updated_by_name", 
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
            "updated_at", "is_unplanned_return", "previous_surgery", "priority", "procedure_type",
            "surgery_grade", "surgery_grade_name", "encounter_type", "encounter",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
