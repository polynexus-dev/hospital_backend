from rest_framework import serializers

from .models import ClinicalNote, Diagnosis, Encounter, InvestigationOrder, OPDTriage, VitalsReading


class EncounterSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)
    doctor_name = serializers.CharField(source="doctor.name", read_only=True)
    case_type = serializers.SerializerMethodField()

    class Meta:
        model = Encounter
        fields = ["id", "appointment", "patient", "patient_name", "doctor", "doctor_name", "department", "created_at", "case_type"]
        read_only_fields = ["id", "appointment", "patient", "doctor", "department", "created_at"]

    def get_case_type(self, obj):
        """new_complaint | old_complaint, from the front-desk registration (blank if none)."""
        visit = getattr(obj.appointment, "opd_visit", None) if obj.appointment_id else None
        return visit.case_type if visit else ""


VITAL_FIELDS = ("height_cm", "weight_kg", "bp_systolic", "bp_diastolic", "pulse", "temperature_c", "spo2")


class OPDTriageSerializer(serializers.ModelSerializer):
    """Vitals are written as plain fields here and stored in the linked VitalsReading."""

    height_cm = serializers.DecimalField(max_digits=5, decimal_places=1, required=False, allow_null=True)
    weight_kg = serializers.DecimalField(max_digits=5, decimal_places=1, required=False, allow_null=True)
    bp_systolic = serializers.IntegerField(required=False, allow_null=True, min_value=40, max_value=300)
    bp_diastolic = serializers.IntegerField(required=False, allow_null=True, min_value=20, max_value=200)
    pulse = serializers.IntegerField(required=False, allow_null=True, min_value=20, max_value=250)
    temperature_c = serializers.DecimalField(max_digits=4, decimal_places=1, required=False, allow_null=True)
    spo2 = serializers.IntegerField(required=False, allow_null=True, min_value=50, max_value=100)
    pain_score = serializers.IntegerField(required=False, allow_null=True, min_value=0, max_value=10)
    recorded_by_name = serializers.SerializerMethodField()
    updated_by_name = serializers.SerializerMethodField()
    pain_level = serializers.SerializerMethodField()

    class Meta:
        model = OPDTriage
        fields = [
            "id", "encounter", "chief_complaint", "complaint_duration", "respiratory_rate", "blood_sugar_mg_dl", "pain_score", "pain_level",
            "allergies_checked", "priority", "notes", *VITAL_FIELDS, "recorded_by", "recorded_by_name", "updated_by", "updated_by_name",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "recorded_by", "updated_by", "created_at", "updated_at"]

    @staticmethod
    def _name(u):
        return (u.get_full_name() or u.email) if u else ""

    def get_recorded_by_name(self, obj):
        return self._name(obj.recorded_by)

    def get_updated_by_name(self, obj):
        return self._name(obj.updated_by)

    def get_pain_level(self, obj):
        from apps.clinical.scoring import numeric_pain

        return numeric_pain({"score": obj.pain_score})[1] if obj.pain_score is not None else ""

    def to_representation(self, obj):
        data = super().to_representation(obj)
        v = obj.vitals
        for f in VITAL_FIELDS:
            val = getattr(v, f, None) if v else None
            data[f] = str(val) if val is not None and f in ("height_cm", "weight_kg", "temperature_c") else val
        return data

    def validate_encounter(self, encounter):
        if self.instance is None and OPDTriage.objects.filter(encounter=encounter).exists():
            raise serializers.ValidationError("This visit already has a triage record — edit it instead.")
        return encounter


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
