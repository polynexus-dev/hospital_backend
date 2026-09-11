from rest_framework import serializers

from .models import AuditLog


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
