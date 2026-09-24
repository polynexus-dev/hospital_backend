from rest_framework import serializers

from apps.patients.serializers import PatientSerializer
from .models import EDVisit, Triage


class TriageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Triage
        fields = [
            "id",
            "ed_visit",
            "triage_category",
            "vitals_summary",
            "triaged_by",
            "triaged_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "triaged_by", "triaged_at", "created_at", "updated_at"]


class EDVisitSerializer(serializers.ModelSerializer):
    patient_detail = PatientSerializer(source="patient", read_only=True)
    triage = TriageSerializer(read_only=True)

    class Meta:
        model = EDVisit
        fields = [
            "id",
            "patient",
            "patient_detail",
            "status",
            "chief_complaint",
            "arrived_at",
            "triage",
            "created_at",
            "updated_at", "mode_of_arrival", "brought_by", "is_unidentified", "ambulance_trip", "disposition_at", "police_station", "police_intimated_at", "police_officer_name", "injuries_description", "is_mlc", "mlc_number", "mlc_type", "mlc_checklist", "mlc_marked_by", "mlc_marked_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "is_mlc", "mlc_number", "mlc_type", "mlc_checklist", "mlc_marked_by", "mlc_marked_at"]
