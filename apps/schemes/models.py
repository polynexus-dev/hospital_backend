"""Government health schemes — PM-JAY (Ayushman Bharat), CGHS, ECHS, ESIC and
state schemes.

Two billing modes:

* package   — PM-JAY style: the hospital is paid the Health Benefit Package
              rate, all-inclusive. The itemised bill is kept for the record;
              the difference is a scheme adjustment, so the patient owes
              nothing for covered care.
* rate_list — CGHS / ECHS style: every service is billed at the scheme's
              rate (the tariff's `category_rates[<scheme code>]`), and the
              scheme pays all of it less any co-pay.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel

DEFAULT_CHECKLISTS = {
    "package": [
        "Beneficiary e-card / PM-JAY ID", "Pre-authorisation approval", "Clinical notes & admission note",
        "Investigation reports supporting diagnosis", "Procedure / operation notes", "Pre- and post-procedure photographs",
        "Implant invoice & sticker (if any)", "Discharge summary", "Final bill", "Patient feedback / beneficiary signature",
    ],
    "rate_list": [
        "Scheme card copy", "Referral / permission letter", "Emergency certificate (if unreferred)",
        "Investigation reports", "Discharge summary", "Final bill at scheme rates", "Implant invoice (if any)",
    ],
}


class GovtScheme(TenantScopedModel):
    class BillingMode(models.TextChoices):
        PACKAGE = "package", "Package rates (all-inclusive, e.g. PM-JAY)"
        RATE_LIST = "rate_list", "Scheme rate list (e.g. CGHS / ECHS)"

    code = models.SlugField(max_length=12, help_text="Short code, also the patient category used for scheme rates, e.g. pmjay, cghs")
    name = models.CharField(max_length=150)
    payer = models.CharField(max_length=150, blank=True, help_text="e.g. State Health Agency, CGHS Directorate")
    billing_mode = models.CharField(max_length=10, choices=BillingMode.choices, default=BillingMode.PACKAGE)
    empanelment_number = models.CharField(max_length=60, blank=True, help_text="The hospital's empanelment / HOSP ID")
    preauth_required = models.BooleanField(default=True)
    claim_submission_days = models.PositiveSmallIntegerField(default=15, help_text="Days after discharge to submit the claim")
    copay_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    document_checklist = models.JSONField(default=list, blank=True)
    claim_portal_url = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["hospital", "code"], name="unique_scheme_code_per_hospital")]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.document_checklist:
            self.document_checklist = DEFAULT_CHECKLISTS[self.billing_mode]
        super().save(*args, **kwargs)


class SchemePackage(TenantScopedModel):
    """A Health Benefit Package (or scheme procedure) the hospital is empanelled for."""

    scheme = models.ForeignKey(GovtScheme, on_delete=models.CASCADE, related_name="packages")
    code = models.CharField(max_length=30, help_text="e.g. HBP 2.0 code SG039A")
    name = models.CharField(max_length=255)
    specialty = models.CharField(max_length=100, blank=True)
    rate = models.DecimalField(max_digits=12, decimal_places=2)
    expected_los_days = models.PositiveSmallIntegerField(null=True, blank=True)
    includes = models.TextField(blank=True, help_text="What the package covers — stay, drugs, implants, follow-up")
    implant_included = models.BooleanField(default=True)
    preauth_required = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["scheme__name", "code"]
        constraints = [models.UniqueConstraint(fields=["scheme", "code"], name="unique_package_code_per_scheme")]

    def __str__(self):
        return f"{self.code} — {self.name}"


class SchemeBeneficiary(TenantScopedModel):
    """A patient's membership of a scheme (card / beneficiary ID)."""

    class Eligibility(models.TextChoices):
        UNVERIFIED = "unverified", "Not verified"
        ELIGIBLE = "eligible", "Eligible"
        INELIGIBLE = "ineligible", "Not eligible"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="scheme_memberships")
    scheme = models.ForeignKey(GovtScheme, on_delete=models.PROTECT, related_name="beneficiaries")
    beneficiary_id = models.CharField(max_length=60, help_text="PM-JAY ID / CGHS card no. / ECHS card no.")
    family_id = models.CharField(max_length=60, blank=True)
    relation = models.CharField(max_length=30, blank=True, help_text="self, spouse, son, …")
    card_valid_to = models.DateField(null=True, blank=True)
    eligibility = models.CharField(max_length=10, choices=Eligibility.choices, default=Eligibility.UNVERIFIED)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["scheme", "beneficiary_id"], name="unique_beneficiary_per_scheme")]

    def __str__(self):
        return f"{self.scheme.code.upper()} {self.beneficiary_id}"


class SchemeCase(TenantScopedModel):
    """One treatment episode claimed from a scheme: pre-auth → treatment → claim → settlement."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PREAUTH_SUBMITTED = "preauth_submitted", "Pre-auth submitted"
        PREAUTH_QUERY = "preauth_query", "Pre-auth query"
        PREAUTH_APPROVED = "preauth_approved", "Pre-auth approved"
        PREAUTH_REJECTED = "preauth_rejected", "Pre-auth rejected"
        DISCHARGED = "discharged", "Discharged — claim pending"
        CLAIM_SUBMITTED = "claim_submitted", "Claim submitted"
        CLAIM_QUERY = "claim_query", "Claim query"
        CLAIM_APPROVED = "claim_approved", "Claim approved"
        CLAIM_REJECTED = "claim_rejected", "Claim rejected"
        SETTLED = "settled", "Settled"

    beneficiary = models.ForeignKey(SchemeBeneficiary, on_delete=models.PROTECT, related_name="cases")
    admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="scheme_cases")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    diagnosis = models.CharField(max_length=255, blank=True)

    preauth_number = models.CharField(max_length=60, blank=True)
    preauth_submitted_at = models.DateTimeField(null=True, blank=True)
    preauth_amount_requested = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    preauth_amount_approved = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    preauth_decided_at = models.DateTimeField(null=True, blank=True)

    claim_number = models.CharField(max_length=60, blank=True)
    claim_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Computed when applied to the bill")
    claim_due_by = models.DateField(null=True, blank=True)
    claim_submitted_at = models.DateTimeField(null=True, blank=True)
    approved_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    deduction_reason = models.CharField(max_length=255, blank=True)
    settled_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    settled_on = models.DateField(null=True, blank=True)
    utr_number = models.CharField(max_length=40, blank=True)

    documents = models.JSONField(default=dict, blank=True, help_text="{checklist item: true/false}")
    history = models.JSONField(default=list, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["hospital", "status"])]

    @property
    def scheme(self):
        return self.beneficiary.scheme

    def log(self, user, old, new, note=""):
        self.history = [*self.history, {
            "at": timezone.now().isoformat(), "by": getattr(user, "email", "") or "system", "from": old, "to": new, "note": note,
        }]


class SchemeCasePackage(models.Model):
    """A package booked on a case, with its rate frozen at the time."""

    case = models.ForeignKey(SchemeCase, on_delete=models.CASCADE, related_name="case_packages")
    package = models.ForeignKey(SchemePackage, on_delete=models.PROTECT, related_name="+")
    quantity = models.PositiveSmallIntegerField(default=1)
    rate = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["case", "package"], name="unique_package_per_case")]
