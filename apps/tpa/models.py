from decimal import Decimal

from django.db import models
from apps.core.encryption import EncryptedTextField, compute_blind_index
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

    # Encrypted at rest (apps.core.encryption) — insurance policy numbers
    # are sensitive personal data under the DPDP Act / SPDI Rules, same as
    # Patient.insurance_policy_number. policy_number_lookup is a
    # deterministic HMAC of the same value, kept in sync in save() below,
    # so exact-match search still works despite the encrypted column being
    # unsearchable — see PreAuthRequestViewSet.get_queryset(). Never add
    # policy_number itself back to filterset_fields/search_fields.
    policy_number = EncryptedTextField()
    policy_number_lookup = models.CharField(max_length=64, db_index=True, editable=False, blank=True)
    claim_amount = models.DecimalField(max_digits=12, decimal_places=2)
    approved_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.SUBMITTED)
    
    checklist = models.JSONField(default=dict, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at"]

    def save(self, *args, **kwargs):
        self.policy_number_lookup = compute_blind_index(self.policy_number) if self.policy_number else ""
        super().save(*args, **kwargs)

    def __str__(self):
        return f"PreAuth #{self.id}: {self.patient.full_name} ({self.tpa_company.name})"


class Claim(TenantScopedModel):
    """Post-treatment claim submission and settlement tracking — the
    continuation PreAuthRequest deliberately stops short of: pre-auth only
    covers the pre-treatment approval, not what the insurer actually pays
    out afterwards. Optionally linked back to the PreAuthRequest that
    preceded it, but not required (cashless emergency admissions can skip
    pre-auth and go straight to a claim)."""

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Claim Submitted"
        UNDER_REVIEW = "under_review", "Under Review"
        SETTLED = "settled", "Settled"
        PARTIALLY_SETTLED = "partially_settled", "Partially Settled"
        REJECTED = "rejected", "Rejected"

    preauth_request = models.ForeignKey(PreAuthRequest, on_delete=models.SET_NULL, null=True, blank=True, related_name="claims")
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="claims")
    tpa_company = models.ForeignKey(TPACompany, on_delete=models.CASCADE, related_name="claims")

    claim_number = models.CharField(max_length=100, blank=True)
    billed_amount = models.DecimalField(max_digits=12, decimal_places=2)
    settled_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.SUBMITTED)
    rejection_reason = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)

    submitted_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [models.Index(fields=["hospital", "status"])]

    @property
    def outstanding_amount(self):
        return self.billed_amount - self.settled_amount

    def __str__(self):
        return f"Claim #{self.id}: {self.patient.full_name} ({self.tpa_company.name})"
