from rest_framework import serializers

from .models import BroadcastCampaign, Channel, ConsentOptOut, Message, Template, Thread


class TemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Template
        fields = ["id", "name", "purpose", "channel", "language", "subject", "body", "is_active"]
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

    class Meta:
        model = Thread
        fields = [
            "id", "patient", "enquiry", "channel", "owner", "status",
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
            "delivered_count", "read_count", "failed_count",
            "created_by", "created_by_name", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "total_recipients", "sent_count", "delivered_count",
            "read_count", "failed_count", "created_by", "created_at", "updated_at",
        ]

    def get_created_by_name(self, obj) -> str:
        if not obj.created_by:
            return "System"
        return obj.created_by.get_full_name() or obj.created_by.email

