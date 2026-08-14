from django.db import models
from apps.core.models import TenantScopedModel
from apps.enquiries.models import Enquiry
from apps.patients.models import Patient


class HealthPackage(TenantScopedModel):
    """Health check package catalogue (§2)."""

    class Category(models.TextChoices):
        CARDIAC = "cardiac", "Cardiac Health Check"
        FULL_BODY = "full_body", "Comprehensive Full Body"
        ORTHO = "ortho", "Bone & Joint Package"
        MATERNITY = "maternity", "Maternity & Women Care"

    name = models.CharField(max_length=200)
    code = models.CharField(max_length=32)
    category = models.CharField(max_length=32, choices=Category.choices, default=Category.FULL_BODY)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField(blank=True)
    included_tests = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} (Rs. {self.price})"


class Campaign(TenantScopedModel):
    """Marketing camp & campaign ROI tracking."""

    class CampaignType(models.TextChoices):
        HEALTH_CAMP = "health_camp", "On-site Health Camp"
        DIGITAL_AD = "digital_ad", "Digital Ad (Meta/Google)"
        CORPORATE_TIEUP = "corporate_tieup", "Corporate Health Tie-Up"

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"

    name = models.CharField(max_length=200)
    campaign_type = models.CharField(max_length=32, choices=CampaignType.choices, default=CampaignType.HEALTH_CAMP)
    budget = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    actual_spend = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return self.name


class CorporateClient(TenantScopedModel):
    """Corporate wellness / employee health-checkup contract — the actual
    commercial agreement (roster size, discount tier, validity window) a
    `Campaign` of type CORPORATE_TIEUP sells against. Kept as its own model
    rather than fields on Campaign because one corporate relationship
    typically spans multiple campaigns/years."""

    class BillingModel(models.TextChoices):
        PER_EMPLOYEE = "per_employee", "Per employee checkup"
        FLAT_ANNUAL = "flat_annual", "Flat annual contract"
        CREDIT_LINE = "credit_line", "Pre-paid credit line"

    name = models.CharField(max_length=200)
    contact_person = models.CharField(max_length=150, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    contact_email = models.EmailField(blank=True)

    campaign = models.ForeignKey(Campaign, on_delete=models.SET_NULL, null=True, blank=True, related_name="corporate_clients")
    billing_model = models.CharField(max_length=16, choices=BillingModel.choices, default=BillingModel.PER_EMPLOYEE)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)

    contract_start = models.DateField()
    contract_end = models.DateField(null=True, blank=True)
    employee_count = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class CampRegistration(TenantScopedModel):
    """Camp registration funnel (Registered ➔ Attended ➔ OPD ➔ IPD)."""

    class FunnelStage(models.TextChoices):
        REGISTERED = "registered", "Registered"
        ATTENDED = "attended", "Attended Camp"
        OPD_CONVERTED = "opd_converted", "OPD Converted"
        IPD_CONVERTED = "ipd_converted", "IPD Converted"

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="registrations")
    patient_name = models.CharField(max_length=200)
    mobile = models.CharField(max_length=32)
    stage = models.CharField(max_length=24, choices=FunnelStage.choices, default=FunnelStage.REGISTERED)
    revenue_generated = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

    # Links the camp attendee to the CRM lead/patient record it became, so
    # campaign spend can actually be traced through to a real conversion
    # instead of stopping at a free-text name/mobile.
    enquiry = models.ForeignKey(Enquiry, on_delete=models.SET_NULL, null=True, blank=True, related_name="camp_registrations")
    patient = models.ForeignKey(Patient, on_delete=models.SET_NULL, null=True, blank=True, related_name="camp_registrations")
    corporate_client = models.ForeignKey(CorporateClient, on_delete=models.SET_NULL, null=True, blank=True, related_name="camp_registrations")

    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-registered_at"]

    def __str__(self):
        return f"{self.patient_name} - {self.campaign.name} ({self.stage})"
