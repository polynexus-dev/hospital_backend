from rest_framework import serializers

from .models import DataRightsRequest, GrievanceTicket, Nominee


class DataRightsRequestSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True)

    class Meta:
        model = DataRightsRequest
        fields = [
            "id", "patient", "patient_name", "request_type", "status", "channel", "details",
            "submitted_at", "sla_due_at", "verified_at", "verified_by",
            "handled_by", "resolution_notes", "resolved_at",
        ]
        # status/verified_*/handled_by/resolution_notes/resolved_at only
        # change through DataRightsRequestViewSet's verify/complete/reject
        # actions — see those docstrings for why a bare PATCH doesn't apply
        # here (mirrors EmergencyAccessLog's reviewed/reviewed_by pattern).
        read_only_fields = [
            "id", "status", "submitted_at", "sla_due_at",
            "verified_at", "verified_by", "handled_by", "resolution_notes", "resolved_at",
        ]


class GrievanceTicketSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source="patient.full_name", read_only=True, default=None)

    class Meta:
        model = GrievanceTicket
        fields = [
            "id", "patient", "patient_name", "subject", "description", "status", "priority",
            "assigned_to", "submitted_at", "sla_due_at", "resolution", "resolved_at",
        ]
        read_only_fields = ["id", "status", "submitted_at", "sla_due_at", "resolution", "resolved_at"]


class NomineeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Nominee
        fields = ["id", "patient", "name", "relationship", "phone", "email", "is_active", "verified_at", "verified_by"]
        read_only_fields = ["id", "verified_at", "verified_by"]
