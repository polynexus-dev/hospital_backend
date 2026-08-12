from django.conf import settings
from django.db import models
from apps.core.models import Department, TenantScopedModel
from apps.patients.models import Patient


class ReferringDoctor(TenantScopedModel):
    """Directory of external referring doctors (§1)."""

    class Tier(models.TextChoices):
        GOLD = "gold", "Gold Tier"
        SILVER = "silver", "Silver Tier"
        BRONZE = "bronze", "Bronze Tier"

    name = models.CharField(max_length=200)
    speciality = models.CharField(max_length=150, blank=True)
    clinic_name = models.CharField(max_length=200, blank=True)
    mobile = models.CharField(max_length=32)
    email = models.EmailField(blank=True)
    city = models.CharField(max_length=100, default="Pune")
    tier = models.CharField(max_length=16, choices=Tier.choices, default=Tier.SILVER)
    
    date_of_birth = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.clinic_name or self.city})"


class ReferralRecord(TenantScopedModel):
    """Patient referral transaction & revenue attribution."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending Visit"
        CONVERTED = "converted", "OPD/IPD Converted"
        PAID = "paid", "Statement Settled"

    referring_doctor = models.ForeignKey(ReferringDoctor, on_delete=models.CASCADE, related_name="referrals")
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="referral_records")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="referral_records")
    
    attributed_revenue = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    commission_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=10.00)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    
    referred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-referred_at"]

    def __str__(self):
        return f"Referral: {self.referring_doctor.name} ➔ {self.patient.full_name}"


class FieldVisit(TenantScopedModel):
    """Field representative touchpoint visit logs."""

    referring_doctor = models.ForeignKey(ReferringDoctor, on_delete=models.CASCADE, related_name="field_visits")
    visited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    visit_date = models.DateField()
    notes = models.TextField()
    outcome = models.CharField(max_length=200, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-visit_date"]

    def __str__(self):
        return f"Visit to {self.referring_doctor.name} on {self.visit_date}"
