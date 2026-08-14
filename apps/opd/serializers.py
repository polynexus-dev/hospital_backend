from rest_framework import serializers

from .models import ClinicalNote, Diagnosis, Encounter, InvestigationOrder, VitalsReading


class EncounterSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    doctor_name = serializers.CharField(source="doctor.name", read_only=True)

    class Meta:
        model = Encounter
        fields = ["id", "appointment", "patient", "patient_name", "doctor", "doctor_name", "department", "created_at"]
        read_only_fields = ["id", "appointment", "patient", "doctor", "department", "created_at"]


class VitalsReadingSerializer(serializers.ModelSerializer):
    class Meta:
        model = VitalsReading
        fields = [
            "id", "encounter", "recorded_by", "height_cm", "weight_kg",
            "bp_systolic", "bp_diastolic", "pulse", "temperature_c", "spo2", "recorded_at",
        ]
        read_only_fields = ["id", "recorded_by", "recorded_at"]


class ClinicalNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClinicalNote
        fields = [
            "id", "encounter", "doctor", "chief_complaints", "history", "examination_findings",
            "finalized_at", "finalized_by", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "doctor", "finalized_at", "finalized_by", "created_at", "updated_at"]


class DiagnosisSerializer(serializers.ModelSerializer):
    class Meta:
        model = Diagnosis
        fields = [
            "id", "encounter", "icd_code", "description", "diagnosis_type", "created_by",
            "finalized_at", "finalized_by", "created_at",
        ]
        read_only_fields = ["id", "created_by", "finalized_at", "finalized_by", "created_at"]


class InvestigationOrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvestigationOrder
        fields = ["id", "encounter", "order_type", "description", "status", "created_by", "created_at"]
        read_only_fields = ["id", "created_by", "created_at"]
