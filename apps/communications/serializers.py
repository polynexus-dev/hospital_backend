from rest_framework import serializers

from .models import BroadcastCampaign, Channel, ConsentOptOut, Message, Template, Thread


class TemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Template
        fields = ["id", "name", "purpose", "channel", "language", "subject", "body", "is_active", "provider_template_id", "provider_variables"]
        read_only_fields = ["id"]


class ConsentOptOutSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsentOptOut
        fields = ["id", "patient", "channel", "purpose", "is_opted_out", "recorded_by", "updated_at"]
        read_only_fields = ["id", "recorded_by", "updated_at"]


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = [
            "id", "patient", "enquiry", "channel", "direction", "template", "body", "status",
            "sent_by", "provider_message_id", "sent_at", "delivered_at", "read_at", "created_at",
        ]
        read_only_fields = ["id", "status", "provider_message_id", "sent_at", "delivered_at", "read_at", "created_at"]

    def validate(self, attrs):
        patient = attrs.get("patient", getattr(self.instance, "patient", None))
        enquiry = attrs.get("enquiry", getattr(self.instance, "enquiry", None))
        if patient is None and enquiry is None:
            raise serializers.ValidationError("Either patient or enquiry is required.")
        return attrs


class ThreadSerializer(serializers.ModelSerializer):
    unread_count = serializers.SerializerMethodField()
    enquiry_name = serializers.CharField(source="enquiry.name", read_only=True, default=None)

    class Meta:
        model = Thread
        fields = [
            "id", "patient", "enquiry", "enquiry_name", "channel", "owner", "status",
            "last_message_at", "last_inbound_at", "sla_due_at", "unread_count", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "last_message_at", "last_inbound_at", "sla_due_at", "created_at", "updated_at"]

    def validate(self, attrs):
        patient = attrs.get("patient", getattr(self.instance, "patient", None))
        enquiry = attrs.get("enquiry", getattr(self.instance, "enquiry", None))
        if patient is None and enquiry is None:
            raise serializers.ValidationError("Either patient or enquiry is required.")
        return attrs

    def get_unread_count(self, obj) -> int:
        scope = {"patient": obj.patient} if obj.patient_id else {"enquiry": obj.enquiry}
        return Message.objects.filter(
            channel=obj.channel, direction=Message.Direction.INBOUND, is_read=False, **scope
        ).count()


class SendMessageSerializer(serializers.Serializer):
    patient = serializers.IntegerField()
    channel = serializers.ChoiceField(choices=Channel.choices)
    purpose = serializers.CharField()
    context = serializers.DictField(child=serializers.CharField(), required=False, default=dict)
    fallback_channel = serializers.ChoiceField(choices=Channel.choices, required=False, allow_null=True, default=None)


class BroadcastCampaignSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model = BroadcastCampaign
        fields = [
            "id", "title", "channel", "target_audience", "template", "custom_message",
            "scheduled_for", "status", "total_recipients", "sent_count",
            "delivered_count", "read_count", "failed_count", "skipped_count",
            "started_at", "completed_at", "error", "delivery_reported",
            "created_by", "created_by_name", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "status", "total_recipients", "sent_count", "delivered_count", "read_count", "failed_count", "skipped_count",
            "started_at", "completed_at", "error", "created_by", "created_at", "updated_at",
        ]

    delivery_reported = serializers.SerializerMethodField()

    def get_delivery_reported(self, obj) -> bool:
        """Whether delivered / read counts come back for this channel: MSG91
        sends SMS delivery reports; email (SMTP) and WhatsApp don't report
        here yet, so their delivered / read figures aren't real numbers."""
        from django.conf import settings

        return obj.channel == "sms" and settings.SMS_PROVIDER == "msg91"

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        template = attrs.get("template")
        if template is not None and request is not None and template.hospital_id != request.user.hospital_id:
            raise serializers.ValidationError({"template": "Refers to a record from a different hospital."})
        return attrs

    def get_created_by_name(self, obj) -> str:
        if not obj.created_by:
            return "System"
        return obj.created_by.get_full_name() or obj.created_by.email

