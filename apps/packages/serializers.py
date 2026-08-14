from rest_framework import serializers
from .models import CampRegistration, Campaign, CorporateClient, HealthPackage


class HealthPackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = HealthPackage
        fields = ["id", "name", "code", "category", "price", "description", "included_tests", "is_active"]
        read_only_fields = ["id"]


class CampaignSerializer(serializers.ModelSerializer):
    total_registrations = serializers.IntegerField(read_only=True, default=0)
    total_conversions = serializers.IntegerField(read_only=True, default=0)
    total_revenue_generated = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True, default=0.00)
    cost_per_lead = serializers.SerializerMethodField()
    cost_per_acquisition = serializers.SerializerMethodField()
    roi_percent = serializers.SerializerMethodField()

    class Meta:
        model = Campaign
        fields = [
            "id", "name", "campaign_type", "budget", "actual_spend",
            "status", "start_date", "end_date", "total_registrations", "total_conversions",
            "total_revenue_generated", "cost_per_lead", "cost_per_acquisition", "roi_percent",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_cost_per_lead(self, obj):
        registrations = getattr(obj, "total_registrations", 0) or 0
        if not registrations or not obj.actual_spend:
            return None
        return obj.actual_spend / registrations

    def get_cost_per_acquisition(self, obj):
        conversions = getattr(obj, "total_conversions", 0) or 0
        if not conversions or not obj.actual_spend:
            return None
        return obj.actual_spend / conversions

    def get_roi_percent(self, obj):
        if not obj.actual_spend:
            return None
        revenue = getattr(obj, "total_revenue_generated", None) or 0
        return (revenue - obj.actual_spend) / obj.actual_spend * 100


class CorporateClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = CorporateClient
        fields = [
            "id", "name", "contact_person", "contact_phone", "contact_email",
            "campaign", "billing_model", "discount_percent",
            "contract_start", "contract_end", "employee_count",
            "is_active", "notes", "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class CampRegistrationSerializer(serializers.ModelSerializer):
    campaign_name = serializers.CharField(source="campaign.name", read_only=True)

    class Meta:
        model = CampRegistration
        fields = [
            "id", "campaign", "campaign_name", "patient_name",
            "mobile", "stage", "revenue_generated", "enquiry", "patient", "corporate_client", "registered_at",
        ]
        read_only_fields = ["id", "registered_at"]
