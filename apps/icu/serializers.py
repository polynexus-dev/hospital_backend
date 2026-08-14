from rest_framework import serializers

from apps.appointments.serializers import DoctorSerializer
from apps.ipd.serializers import AdmissionSerializer
from .models import ICUAdmission, ICUDailyProgressNote, VentilatorLog


class VentilatorLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = VentilatorLog
        fields = ["id", "icu_admission", "mode", "ventilator_settings", "recorded_by", "recorded_at", "created_at"]
        read_only_fields = ["id", "recorded_by", "created_at"]


class ICUDailyProgressNoteSerializer(serializers.ModelSerializer):
    doctor_detail = DoctorSerializer(source="doctor", read_only=True)

    class Meta:
        model = ICUDailyProgressNote
        fields = [
            "id",
            "icu_admission",
            "doctor",
            "doctor_detail",
            "note",
            "finalized_at",
            "finalized_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "finalized_at", "finalized_by", "created_at", "updated_at"]


class ICUAdmissionSerializer(serializers.ModelSerializer):
    admission_detail = AdmissionSerializer(source="admission", read_only=True)
    ventilator_logs = VentilatorLogSerializer(many=True, read_only=True)
    progress_notes = ICUDailyProgressNoteSerializer(many=True, read_only=True)

    class Meta:
        model = ICUAdmission
        fields = [
            "id",
            "admission",
            "admission_detail",
            "bed",
            "ventilator_required",
            "admitted_at",
            "discharged_at",
            "ventilator_logs",
            "progress_notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
