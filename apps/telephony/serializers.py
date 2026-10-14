from rest_framework import serializers

from apps.core.crud import TenantModelSerializer

from .models import Call, CallbackTask, IVRMenu, IVRNode, IVRRoute

AI_FIELDS = [
    "ai_summary", "ai_lead_class", "ai_booking_intent", "ai_status", "ai_error", "ai_processed_at",
    "missed_opportunity", "missed_opportunity_at",
]


class CallSerializer(serializers.ModelSerializer):
    class Meta:
        model = Call
        fields = [
            "id", "direction", "status", "from_number", "to_number",
            "patient", "enquiry", "department", "operator",
            "started_at", "answered_at", "ended_at", "duration_seconds",
            "recording_url", "consent_recorded", "ivr_path", "call_reason", "notes",
            "provider_name", "provider_call_id", "created_at", *AI_FIELDS,
        ]
        read_only_fields = ["id", "created_at", *AI_FIELDS]


class CallbackTaskSerializer(serializers.ModelSerializer):
    ivr_path = serializers.CharField(source="call.ivr_path", read_only=True, default="")

    class Meta:
        model = CallbackTask
        fields = [
            "id", "call", "patient", "phone_number", "department",
            "owner", "status", "sla_due_at", "escalation_level", "attempt_count",
            "notes", "resolved_at", "created_at", "ivr_path", "source",
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
            "created_at", *AI_FIELDS,
        ]
        read_only_fields = fields


class TelephonyWebhookSerializer(serializers.Serializer):
    """Loose passthrough — the actual field shape depends on the provider
    and is normalized by apps.telephony.adapters before it reaches the DB."""

    def to_internal_value(self, data):
        return data


class CallAnalysisSerializer(serializers.ModelSerializer):
    class Meta:
        model = Call
        fields = ["id", "transcript", "call_reason", *AI_FIELDS]
        read_only_fields = fields


class IVRNodeSerializer(TenantModelSerializer):
    DIGITS = set("0123456789*#")

    class Meta:
        model = IVRNode
        fields = ["id", "menu", "parent", "digit", "label", "action", "prompt", "language", "department",
                  "queue_target", "queue_agents", "sort_order"]
        extra_kwargs = {"digit": {"required": False}}  # empty for the welcome menu
        validators = []  # DRF's (parent, digit) unique validator would force a key on the welcome menu too; see validate()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        menu, parent, digit, action = get("menu"), get("parent"), get("digit") or "", get("action")
        if parent is not None:
            if parent.menu_id != menu.pk:
                raise serializers.ValidationError({"parent": "Belongs to a different phone menu."})
            if self.instance is not None and (parent.pk == self.instance.pk or self._is_descendant(parent)):
                raise serializers.ValidationError({"parent": "An option can't sit under itself."})
            if digit not in self.DIGITS:
                raise serializers.ValidationError({"digit": "Pick the key the caller presses: 0-9, * or #."})
            siblings = IVRNode.objects.filter(parent=parent, digit=digit)
            if self.instance is not None:
                siblings = siblings.exclude(pk=self.instance.pk)
            if siblings.exists():
                raise serializers.ValidationError({"digit": f"Key {digit} is already used at this level."})
        else:
            roots = IVRNode.objects.filter(menu=menu, parent__isnull=True)
            if self.instance is not None:
                roots = roots.exclude(pk=self.instance.pk)
            if roots.exists():
                raise serializers.ValidationError({"parent": "This menu already has a welcome menu; add options under it."})
        if action == IVRNode.Action.QUEUE and not get("queue_target"):
            raise serializers.ValidationError({"queue_target": "Name the PBX queue, ring group or extension to connect to."})
        if action == IVRNode.Action.LANGUAGE and get("language") not in ("en", "hi", "mr"):
            raise serializers.ValidationError({"language": "Choose en, hi or mr."})
        if action == IVRNode.Action.INFO and not (get("prompt") or "").strip():
            raise serializers.ValidationError({"prompt": "Write the message the caller should hear."})
        return attrs

    def _is_descendant(self, node):
        seen = set()
        while node is not None and node.pk not in seen:
            if node.pk == self.instance.pk:
                return True
            seen.add(node.pk)
            node = node.parent
        return False


class IVRMenuSerializer(TenantModelSerializer):
    nodes = IVRNodeSerializer(many=True, read_only=True)
    webhooks = serializers.SerializerMethodField()

    class Meta:
        model = IVRMenu
        fields = ["id", "name", "dial_in_number", "is_active", "fallback_target", "hold_announce_seconds",
                  "hold_info_text", "booking_url", "nodes", "webhooks", "created_at"]

    def get_webhooks(self, obj):
        request = self.context.get("request")
        base = f"/api/v1/ivr/{obj.webhook_token}"
        absolute = request.build_absolute_uri if request is not None else (lambda u: u)
        return {name: absolute(f"{base}/{name}/") for name in ("step", "hold", "event")}
