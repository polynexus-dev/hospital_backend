from rest_framework import serializers

from .models import Call, CallbackTask, IVRRoute


class CallSerializer(serializers.ModelSerializer):
    class Meta:
        model = Call
        fields = [
            "id", "direction", "status", "from_number", "to_number",
            "patient", "enquiry", "department", "operator",
            "started_at", "answered_at", "ended_at", "duration_seconds",
            "recording_url", "consent_recorded", "ivr_path", "call_reason", "notes",
            "provider_name", "provider_call_id", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class CallbackTaskSerializer(serializers.ModelSerializer):
    ivr_path = serializers.CharField(source="call.ivr_path", read_only=True, default="")

    class Meta:
        model = CallbackTask
        fields = [
            "id", "call", "patient", "phone_number", "department",
            "owner", "status", "sla_due_at", "escalation_level", "attempt_count",
            "notes", "resolved_at", "created_at", "ivr_path",
        ]
        read_only_fields = ["id", "escalation_level", "attempt_count", "created_at"]


class IVRRouteSerializer(serializers.ModelSerializer):
    class Meta:
        model = IVRRoute
        fields = ["id", "department", "language", "dial_in_number", "ivr_option_code", "description", "is_active"]
        read_only_fields = ["id"]


class ClickToCallSerializer(serializers.Serializer):
    to_number = serializers.CharField()
    patient = serializers.IntegerField(required=False, allow_null=True)


class CallHistorySerializer(serializers.ModelSerializer):
    """Read-only serializer used by the inbound / outbound call-history
    endpoints.  Adds human-readable labels for FK relations so the frontend
    doesn't need a follow-up lookup per row."""

    direction_display = serializers.CharField(source="get_direction_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    call_reason_display = serializers.CharField(source="get_call_reason_display", read_only=True)

    operator_name = serializers.SerializerMethodField()
    patient_name = serializers.SerializerMethodField()
    department_name = serializers.SerializerMethodField()

    def get_operator_name(self, obj):
        if obj.operator_id is None:
            return None
        op = obj.operator
        return getattr(op, "get_full_name", lambda: None)() or getattr(op, "email", None)

    def get_patient_name(self, obj):
        if obj.patient_id is None:
            return None
        p = obj.patient
        return getattr(p, "full_name", None) or str(p)

    def get_department_name(self, obj):
        if obj.department_id is None:
            return None
        return getattr(obj.department, "name", None)

    class Meta:
        model = Call
        fields = [
            "id",
            "direction", "direction_display",
            "status", "status_display",
            "from_number", "to_number",
            "patient", "patient_name",
            "enquiry",
            "department", "department_name",
            "operator", "operator_name",
            "started_at", "answered_at", "ended_at", "duration_seconds",
            "recording_url", "consent_recorded",
            "ivr_path", "call_reason", "call_reason_display", "notes",
            "provider_name", "provider_call_id",
            "created_at",
        ]
        read_only_fields = fields


class TelephonyWebhookSerializer(serializers.Serializer):
    """Loose passthrough — the actual field shape depends on the provider
    and is normalized by apps.telephony.adapters before it reaches the DB."""

    def to_internal_value(self, data):
        return data
