from rest_framework import serializers

from .models import DailyMISLog


class DailyMISLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyMISLog
        fields = ["id", "report_date", "summary", "sent_at", "send_error"]
        read_only_fields = fields
