from rest_framework import serializers
from .models import CampRegistration, Campaign, HealthPackage


class HealthPackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = HealthPackage
        fields = ["id", "name", "code", "category", "price", "description", "included_tests", "is_active"]
        read_only_fields = ["id"]


class CampaignSerializer(serializers.ModelSerializer):
    total_registrations = serializers.IntegerField(read_only=True, default=0)
    total_revenue_generated = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True, default=0.00)

    class Meta:
        model = Campaign
        fields = [
            "id", "name", "campaign_type", "budget", "actual_spend",
            "status", "start_date", "end_date", "total_registrations",
            "total_revenue_generated", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class CampRegistrationSerializer(serializers.ModelSerializer):
    campaign_name = serializers.CharField(source="campaign.name", read_only=True)

    class Meta:
        model = CampRegistration
        fields = [
            "id", "campaign", "campaign_name", "patient_name",
            "mobile", "stage", "revenue_generated", "registered_at",
        ]
        read_only_fields = ["id", "registered_at"]
