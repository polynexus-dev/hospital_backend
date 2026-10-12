from rest_framework import serializers

from .models import ConsentNotice, DataRightsRequest, GrievanceTicket, Nominee, PatientConsent


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


class ConsentNoticeSerializer(serializers.ModelSerializer):
    purpose_label = serializers.CharField(source="get_purpose_display", read_only=True)
    consents_count = serializers.IntegerField(source="consents.count", read_only=True)

    class Meta:
        model = ConsentNotice
        fields = ["id", "purpose", "purpose_label", "language", "version", "title", "body", "is_active", "published_by", "created_at", "consents_count"]
        read_only_fields = ["id", "version", "published_by", "created_at"]

    def validate(self, attrs):
        # Published wording is fixed: what a patient agreed to must stay provable.
        if self.instance is not None and set(attrs) - {"is_active"}:
            raise serializers.ValidationError("A published notice can't be edited. Publish a new version instead.")
        return attrs


class PatientConsentSerializer(serializers.ModelSerializer):
    purpose_label = serializers.CharField(source="get_purpose_display", read_only=True)
    notice_version = serializers.IntegerField(source="notice.version", read_only=True)
    captured_by_name = serializers.CharField(source="captured_by.get_full_name", read_only=True, default=None)

    class Meta:
        model = PatientConsent
        fields = [
            "id", "patient", "notice", "notice_version", "purpose", "purpose_label", "status", "method", "given_by", "guardian_name",
            "captured_by", "captured_by_name", "created_at", "withdrawn_at", "withdrawn_by", "withdrawal_reason",
        ]
        read_only_fields = ["id", "purpose", "captured_by", "created_at", "withdrawn_at", "withdrawn_by", "withdrawal_reason"]

    def validate(self, attrs):
        if attrs.get("status") == PatientConsent.Status.WITHDRAWN:
            raise serializers.ValidationError({"status": "Use the withdraw action to withdraw a consent."})
        notice = attrs.get("notice")
        if notice is not None and not notice.is_active:
            raise serializers.ValidationError({"notice": "That notice version has been replaced. Use the current one."})
        if notice is not None and attrs.get("patient") is not None and notice.hospital_id != attrs["patient"].hospital_id:
            raise serializers.ValidationError({"notice": "That notice belongs to a different hospital."})
        if attrs.get("given_by") == PatientConsent.GivenBy.GUARDIAN and not attrs.get("guardian_name"):
            raise serializers.ValidationError({"guardian_name": "Name the parent or guardian giving consent."})
        return attrs
