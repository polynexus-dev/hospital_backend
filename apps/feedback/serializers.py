from rest_framework import serializers

from .models import Complaint, FeedbackRequest, NPSResponse, ServiceRecoveryTask


class FeedbackRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeedbackRequest
        fields = ["id", "patient", "appointment", "doctor", "department", "status", "sent_at", "created_at"]
        read_only_fields = ["id", "status", "sent_at", "created_at"]


class NPSResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = NPSResponse
        fields = ["id", "feedback_request", "patient", "doctor", "department", "score", "category", "comment", "created_at"]
        read_only_fields = ["id", "patient", "doctor", "department", "category", "created_at"]


class SubmitNPSSerializer(serializers.Serializer):
    score = serializers.IntegerField(min_value=0, max_value=10)
    comment = serializers.CharField(required=False, allow_blank=True, default="")


class ComplaintSerializer(serializers.ModelSerializer):
    class Meta:
        model = Complaint
        fields = ["id", "patient", "department", "description", "root_cause", "status", "owner", "closed_at", "created_at"]
        read_only_fields = ["id", "closed_at", "created_at"]


class ServiceRecoveryTaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceRecoveryTask
        fields = ["id", "nps_response", "owner", "status", "sla_due_at", "resolution_notes", "resolved_at"]
        read_only_fields = ["id", "sla_due_at", "resolved_at"]
