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


class TelephonyWebhookSerializer(serializers.Serializer):
    """Loose passthrough — the actual field shape depends on the provider
    and is normalized by apps.telephony.adapters before it reaches the DB."""

    def to_internal_value(self, data):
        return data
