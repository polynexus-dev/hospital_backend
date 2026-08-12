from rest_framework import serializers

from .models import Enquiry, EnquiryStageChange


class EnquirySerializer(serializers.ModelSerializer):
    class Meta:
        model = Enquiry
        fields = [
            "id", "patient", "name", "mobile", "alternate_mobile", "email",
            "source", "campaign", "department", "service_requested", "urgency",
            "stage", "assigned_to", "duplicate_of",
            "sla_due_at", "escalation_level", "lost_reason", "notes",
            "estimated_value",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "assigned_to", "duplicate_of", "sla_due_at", "escalation_level", "created_at", "updated_at"]


class EnquiryStageChangeSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnquiryStageChange
        fields = ["id", "enquiry", "from_stage", "to_stage", "changed_by", "created_at"]
        read_only_fields = fields


class MoveStageSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=Enquiry.Stage.choices)


class LoseEnquirySerializer(serializers.Serializer):
    lost_reason = serializers.CharField()


class BulkImportRowSerializer(serializers.Serializer):
    name = serializers.CharField()
    mobile = serializers.CharField()
    email = serializers.EmailField(required=False, allow_blank=True)
    source = serializers.ChoiceField(choices=Enquiry.Source.choices, default=Enquiry.Source.OTHER)
    service_requested = serializers.CharField(required=False, allow_blank=True, default="")
