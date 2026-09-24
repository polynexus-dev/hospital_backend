from rest_framework import serializers

from apps.core.crud import TenantModelSerializer

from .models import Accreditation, AuditRule, BackupRecord, HelpArticle, ReleaseNote, RetentionPolicy, SecurityEvent, SecurityPolicy

VALID_AUDIT_ACTIONS = {"create", "update", "delete", "read", "request", "export"}


class SecurityPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = SecurityPolicy
        exclude = ["hospital", "created_at", "updated_at"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        if attrs.get("idle_lock_minutes") == 0:
            raise serializers.ValidationError({"idle_lock_minutes": "Must be at least 1 minute."})
        if "password_min_length" in attrs and attrs["password_min_length"] < 8:
            raise serializers.ValidationError({"password_min_length": "Minimum length cannot be below 8."})
        return attrs


class SecurityEventSerializer(serializers.ModelSerializer):
    user_email = serializers.SerializerMethodField()

    class Meta:
        model = SecurityEvent
        fields = [
            "id", "user", "user_email", "username_attempted", "event_type", "severity",
            "ip_address", "user_agent", "path", "details", "created_at",
        ]
        read_only_fields = fields

    def get_user_email(self, obj):
        return obj.user.email if obj.user_id else None


class AuditRuleSerializer(TenantModelSerializer):
    class Meta:
        model = AuditRule
        fields = "__all__"

    def validate_actions(self, value):
        bad = set(value or []) - VALID_AUDIT_ACTIONS
        if bad:
            raise serializers.ValidationError(f"Unknown actions: {sorted(bad)}")
        return value


class RetentionPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = RetentionPolicy
        exclude = ["hospital", "created_at", "updated_at"]
        read_only_fields = ["id"]


class BackupRecordSerializer(serializers.ModelSerializer):
    triggered_by_email = serializers.SerializerMethodField()

    class Meta:
        model = BackupRecord
        exclude = ["hospital", "file_path"]
        read_only_fields = ["id"]

    def get_triggered_by_email(self, obj):
        return obj.triggered_by.email if obj.triggered_by_id else None


class HelpArticleSerializer(serializers.ModelSerializer):
    is_platform_wide = serializers.SerializerMethodField()

    class Meta:
        model = HelpArticle
        exclude = ["hospital"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_is_platform_wide(self, obj):
        return obj.hospital_id is None


class AccreditationSerializer(TenantModelSerializer):
    class Meta:
        model = Accreditation
        fields = "__all__"


class ReleaseNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReleaseNote
        fields = "__all__"
