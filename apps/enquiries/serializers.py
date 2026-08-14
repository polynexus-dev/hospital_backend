from rest_framework import serializers

from apps.accounts.models import User

from .models import Enquiry, EnquiryAssignmentChange, EnquiryStageChange


class EnquirySerializer(serializers.ModelSerializer):
    class Meta:
        model = Enquiry
        fields = [
            "id", "patient", "name", "mobile", "alternate_mobile", "email",
            "source", "campaign",
            "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
            "landing_page", "referrer_url",
            "department", "service_requested", "urgency", "score",
            "stage", "assigned_to", "duplicate_of",
            "sla_due_at", "escalation_level", "lost_reason", "lost_notes", "notes",
            "estimated_value",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "assigned_to", "duplicate_of", "sla_due_at", "escalation_level", "created_at", "updated_at"]


class EnquiryStageChangeSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnquiryStageChange
        fields = ["id", "enquiry", "from_stage", "to_stage", "changed_by", "created_at"]
        read_only_fields = fields


class EnquiryAssignmentChangeSerializer(serializers.ModelSerializer):
    class Meta:
        model = EnquiryAssignmentChange
        fields = ["id", "enquiry", "from_owner", "to_owner", "changed_by", "reason", "created_at"]
        read_only_fields = fields


class MoveStageSerializer(serializers.Serializer):
    stage = serializers.ChoiceField(choices=Enquiry.Stage.choices)


class LoseEnquirySerializer(serializers.Serializer):
    lost_reason = serializers.ChoiceField(choices=Enquiry.LostReason.choices)
    lost_notes = serializers.CharField(required=False, allow_blank=True, default="")


class ReassignEnquirySerializer(serializers.Serializer):
    owner = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    reason = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_owner(self, owner):
        request = self.context.get("request")
        if request is not None and owner.hospital_id != request.user.hospital_id:
            raise serializers.ValidationError("Owner must belong to the same hospital as the enquiry.")
        return owner


class MergeEnquirySerializer(serializers.Serializer):
    primary_id = serializers.PrimaryKeyRelatedField(queryset=Enquiry.objects.all())


class BulkImportRowSerializer(serializers.Serializer):
    name = serializers.CharField()
    mobile = serializers.CharField()
    email = serializers.EmailField(required=False, allow_blank=True)
    source = serializers.ChoiceField(choices=Enquiry.Source.choices, default=Enquiry.Source.OTHER)
    service_requested = serializers.CharField(required=False, allow_blank=True, default="")


class LeadWebhookSerializer(serializers.Serializer):
    """Inbound payload from website forms / Meta & Google lead ads (§2).
    Deliberately permissive — third-party form builders vary in what they
    send, so only name+mobile are required."""

    name = serializers.CharField(max_length=255)
    mobile = serializers.CharField(max_length=20)
    alternate_mobile = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    source = serializers.ChoiceField(choices=Enquiry.Source.choices, default=Enquiry.Source.WEBSITE)
    campaign = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    service_requested = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    utm_source = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    utm_medium = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    utm_campaign = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    utm_term = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    utm_content = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    landing_page = serializers.URLField(max_length=500, required=False, allow_blank=True, default="")
    referrer_url = serializers.URLField(max_length=500, required=False, allow_blank=True, default="")
