from django.db import models
from apps.core.models import TenantScopedModel
from apps.patients.models import Patient


class TPACompany(TenantScopedModel):
    """Directory of Insurers & Third-Party Administrators (§3)."""

    name = models.CharField(max_length=200)
    code = models.CharField(max_length=32)
    contact_person = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    claim_submission_email = models.EmailField(blank=True)
    avg_tat_days = models.PositiveIntegerField(default=2)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class PreAuthRequest(TenantScopedModel):
    """Pre-authorization claim desk & turnaround tracking."""

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Pre-Auth Submitted"
        QUERY_RAISED = "query_raised", "Insurer Query Raised"
        APPROVED = "approved", "Pre-Auth Approved"
        REJECTED = "rejected", "Pre-Auth Rejected"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="preauth_requests")
    tpa_company = models.ForeignKey(TPACompany, on_delete=models.CASCADE, related_name="preauth_requests")
    
    policy_number = models.CharField(max_length=100)
    claim_amount = models.DecimalField(max_digits=12, decimal_places=2)
    approved_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.SUBMITTED)
    
    checklist = models.JSONField(default=dict, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"PreAuth #{self.id}: {self.patient.full_name} ({self.tpa_company.name})"
