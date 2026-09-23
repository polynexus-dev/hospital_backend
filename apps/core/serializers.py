from rest_framework import serializers

from .models import AuditLog, EmergencyAccessLog


class AuditLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = [
            "id", "actor", "actor_email", "action", "model_name", "object_id",
            "object_repr", "changes", "method", "path", "status_code",
            "ip_address", "created_at",
        ]
        read_only_fields = fields

    def get_actor_email(self, obj) -> str | None:
        return obj.actor.email if obj.actor_id else None


class EmergencyAccessLogSerializer(serializers.ModelSerializer):
    actor_email = serializers.SerializerMethodField()
    reviewed_by_email = serializers.SerializerMethodField()

    class Meta:
        model = EmergencyAccessLog
        fields = [
            "id", "actor", "actor_email", "model_name", "object_id", "reason",
            "accessed_at", "reviewed", "reviewed_by", "reviewed_by_email",
            "reviewed_at", "review_notes",
        ]
        # `reviewed`/`review_notes` are stamped together only through
        # EmergencyAccessLogViewSet.mark_reviewed, never a bare PATCH — that
        # keeps reviewed/reviewed_by/reviewed_at from ever drifting out of
        # sync with each other.
        read_only_fields = ["id", "actor", "model_name", "object_id", "reason", "accessed_at", "reviewed", "reviewed_by", "reviewed_at", "review_notes"]

    def get_actor_email(self, obj) -> str | None:
        return obj.actor.email if obj.actor_id else None

    def get_reviewed_by_email(self, obj) -> str | None:
        return obj.reviewed_by.email if obj.reviewed_by_id else None
