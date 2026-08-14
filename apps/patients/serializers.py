from rest_framework import serializers

from .models import Document, Patient, TimelineEvent


class PatientSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Patient
        fields = [
            "id", "first_name", "last_name", "full_name", "date_of_birth", "gender",
            "mobile", "alternate_mobile", "email", "address", "city",
            "national_id_type", "national_id_number",
            "insurance_provider", "insurance_policy_number", "employer",
            "attendant_name", "attendant_phone", "attendant_relation", "referring_doctor_name",
            "guardian", "relationship_to_guardian",
            "next_recall_due_at", "recall_reason",
            "preferred_language", "is_active", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_guardian(self, guardian):
        if guardian is not None and self.instance is not None and guardian.pk == self.instance.pk:
            raise serializers.ValidationError("A patient cannot be their own guardian.")
        return guardian


class PatientLookupSerializer(serializers.ModelSerializer):
    """Minimal shape for the telephony screen-pop — fast, low-payload."""

    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Patient
        fields = ["id", "full_name", "mobile", "preferred_language", "insurance_provider", "referring_doctor_name"]


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = ["id", "patient", "category", "title", "file", "notes", "uploaded_by", "created_at"]
        read_only_fields = ["id", "uploaded_by", "created_at"]


class TimelineEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimelineEvent
        fields = ["id", "patient", "event_type", "summary", "occurred_at", "created_by"]
        read_only_fields = fields


class PrescriptionSerializer(serializers.ModelSerializer):
    doctor_name = serializers.CharField(source="doctor.get_full_name", read_only=True)
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)

    class Meta:
        from .models import Prescription
        model = Prescription
        fields = [
            "id", "patient", "patient_name", "doctor", "doctor_name",
            "diagnosis", "symptoms", "medications", "lab_orders",
            "notes", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

