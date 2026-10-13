from django.conf import settings
from django.db import models

from apps.core.encryption import fill_search_indexes
from apps.core.fields import EncryptedCharField
from apps.core.models import Department, TenantScopedModel
from apps.patients.models import Patient


class Enquiry(TenantScopedModel):
    class Stage(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        SCHEDULED = "scheduled", "Scheduled"
        VISITED = "visited", "Visited"
        COMPLETED = "completed", "Completed"
        FOLLOW_UP = "follow_up", "Follow-up"
        LOST = "lost", "Lost"

    class Source(models.TextChoices):
        IVR = "ivr", "IVR / call"
        WHATSAPP = "whatsapp", "WhatsApp"
        WEBSITE = "website", "Website form"
        WALK_IN = "walk_in", "Walk-in"
        REFERRAL = "referral", "Referral"
        GOOGLE = "google", "Google"
        META = "meta", "Meta"
        OTHER = "other", "Other"

    class Urgency(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class LostReason(models.TextChoices):
        COST = "cost", "Cost too high"
        LOCATION = "location", "Location inconvenient"
        WENT_ELSEWHERE = "went_elsewhere", "Chose another hospital"
        NO_RESPONSE = "no_response", "Could not be reached"
        NOT_INTERESTED = "not_interested", "No longer interested"
        MEDICAL_REASON = "medical_reason", "Medical reason changed"
        DUPLICATE = "duplicate", "Duplicate / test enquiry"
        OTHER = "other", "Other"

    patient = models.ForeignKey(Patient, on_delete=models.SET_NULL, null=True, blank=True, related_name="enquiries")

    # A lead's identity and contact details are encrypted at rest (DPDP Act
    # 2023); the *_hash / name_search columns keep them findable.
    name = EncryptedCharField(max_length=255)
    mobile = EncryptedCharField(max_length=20)
    alternate_mobile = EncryptedCharField(max_length=20, blank=True)
    email = EncryptedCharField(max_length=254, blank=True)
    name_search = models.TextField(blank=True, editable=False)
    mobile_hash = models.CharField(max_length=64, blank=True, editable=False, db_index=True)
    alternate_mobile_hash = models.CharField(max_length=64, blank=True, editable=False, db_index=True)
    email_hash = models.CharField(max_length=64, blank=True, editable=False, db_index=True)

    source = models.CharField(max_length=16, choices=Source.choices)
    campaign = models.CharField(max_length=150, blank=True)

    # Sub-source tracking for paid/organic acquisition reporting — populated
    # from URL query params on website forms and Meta/Google lead-ad payloads.
    utm_source = models.CharField(max_length=100, blank=True)
    utm_medium = models.CharField(max_length=100, blank=True)
    utm_campaign = models.CharField(max_length=150, blank=True)
    utm_term = models.CharField(max_length=150, blank=True)
    utm_content = models.CharField(max_length=150, blank=True)
    landing_page = models.URLField(max_length=500, blank=True)
    referrer_url = models.URLField(max_length=500, blank=True)

    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="enquiries")
    consulting_doctor = models.ForeignKey(
        "appointments.Doctor", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="enquiries", help_text="Doctor who consulted / is scheduled with this lead",
    )
    service_requested = models.CharField(max_length=255, blank=True)
    urgency = models.CharField(max_length=16, choices=Urgency.choices, default=Urgency.NORMAL)

    # Manual/automated qualification score (0-100) — feeds hot/warm/cold
    # sorting on the pipeline board ahead of a scoring-rules engine.
    score = models.PositiveSmallIntegerField(default=0)

    stage = models.CharField(max_length=16, choices=Stage.choices, default=Stage.NEW)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_enquiries")

    duplicate_of = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="duplicates")

    sla_due_at = models.DateTimeField(null=True, blank=True)
    follow_up_date = models.DateField(null=True, blank=True, help_text="Scheduled callback / follow-up date")
    escalation_level = models.PositiveSmallIntegerField(default=0)

    lost_reason = models.CharField(max_length=32, choices=LostReason.choices, blank=True)
    lost_notes = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    estimated_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    SEARCH_INDEXES = {
        "name_search": ("name", ["name"]),
        "mobile_hash": ("phone", ["mobile"]),
        "alternate_mobile_hash": ("phone", ["alternate_mobile"]),
        "email_hash": ("email", ["email"]),
    }

    def save(self, *args, **kwargs):
        columns = fill_search_indexes(self)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | set(columns)
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "enquiries"
        indexes = [
            models.Index(fields=["hospital", "stage", "created_at"]),
            models.Index(fields=["hospital", "mobile"]),
            models.Index(fields=["hospital", "follow_up_date"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_stage_display()})"


class EnquiryStageChange(TenantScopedModel):
    """Audit trail of pipeline movement, separate from the coarse
    AuditLog so the funnel/ageing reports (§12) can query it directly."""

    enquiry = models.ForeignKey(Enquiry, on_delete=models.CASCADE, related_name="stage_changes")
    from_stage = models.CharField(max_length=16, blank=True)
    to_stage = models.CharField(max_length=16)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]


class EnquiryAssignmentChange(TenantScopedModel):
    """Audit trail of lead ownership — who owned this enquiry and when,
    covering both auto-assignment (round-robin) and manual reassignment.
    Mirrors EnquiryStageChange so ownership history is queryable the same
    way pipeline movement already is."""

    enquiry = models.ForeignKey(Enquiry, on_delete=models.CASCADE, related_name="assignment_changes")
    from_owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    to_owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]


class TreatmentEstimate(TenantScopedModel):
    """IPD & Surgical conversion pipeline with cost breakdown & pre-auth tracking."""

    class Stage(models.TextChoices):
        ADVISED = "advised", "Advised by Doctor"
        COUNSELING = "counseling", "Financial Counseling"
        ESTIMATE_SHARED = "estimate_shared", "Estimate Shared"
        PREAUTH_IN_PROGRESS = "preauth_in_progress", "Insurance Pre-Auth"
        SCHEDULED = "scheduled", "Surgery Scheduled"
        CONVERTED = "converted", "Admitted / Converted"
        DROPPED = "dropped", "Dropped / Lost"

    class RoomCategory(models.TextChoices):
        GENERAL = "general", "General Ward"
        SEMI_PRIVATE = "semi_private", "Semi-Private"
        PRIVATE = "private", "Private Room (AC)"
        DELUXE = "deluxe", "Deluxe Suite"

    class PaymentMode(models.TextChoices):
        CASH = "cash", "Self Pay / Cash"
        INSURANCE_CASHLESS = "insurance", "Private Insurance (Cashless)"
        GOVT_SCHEME = "govt_scheme", "Ayushman / CGHS / Govt"
        CORPORATE = "corporate", "Corporate TPA"

    class PreAuthStatus(models.TextChoices):
        NOT_APPLICABLE = "not_applicable", "Not Applicable (Cash)"
        PENDING_DOCS = "pending_docs", "Documents Pending"
        SUBMITTED = "submitted", "Submitted to TPA"
        QUERY_RAISED = "query_raised", "Query Raised by TPA"
        APPROVED = "approved", "Pre-Auth Approved"
        DENIED = "denied", "Pre-Auth Denied"

    class DropReason(models.TextChoices):
        COST_HIGH = "cost_high", "Cost too high"
        FEAR_OF_SURGERY = "fear", "Patient hesitation / fear"
        WENT_ELSEWHERE = "went_elsewhere", "Chose competitor hospital"
        POSTPONED = "postponed", "Postponed indefinitely"
        INSURANCE_REJECTED = "insurance_rejected", "Insurance pre-auth rejected"
        OTHER = "other", "Other"

    patient = models.ForeignKey(Patient, on_delete=models.SET_NULL, null=True, blank=True, related_name="treatment_estimates")
    enquiry = models.ForeignKey(Enquiry, on_delete=models.SET_NULL, null=True, blank=True, related_name="treatment_estimates")
    doctor = models.ForeignKey("appointments.Doctor", on_delete=models.SET_NULL, null=True, blank=True, related_name="treatment_estimates")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="treatment_estimates")

    procedure_name = models.CharField(max_length=255)
    diagnosis = models.CharField(max_length=255, blank=True)
    room_category = models.CharField(max_length=20, choices=RoomCategory.choices, default=RoomCategory.SEMI_PRIVATE)
    stay_days = models.PositiveSmallIntegerField(default=2)

    surgeon_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    ot_charges = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    room_charges = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    medicines_estimate = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    implants_investigations = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    total_estimate = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)

    payment_mode = models.CharField(max_length=20, choices=PaymentMode.choices, default=PaymentMode.INSURANCE_CASHLESS)
    tpa_name = models.CharField(max_length=150, blank=True)
    insurance_preauth_status = models.CharField(max_length=20, choices=PreAuthStatus.choices, default=PreAuthStatus.NOT_APPLICABLE)
    approved_preauth_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    stage = models.CharField(max_length=24, choices=Stage.choices, default=Stage.ADVISED)
    drop_reason = models.CharField(max_length=24, choices=DropReason.choices, blank=True)
    notes = models.TextField(blank=True)
    valid_until = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["hospital", "stage", "created_at"]),
        ]

    def __str__(self):
        return f"Estimate: {self.procedure_name} ({self.get_stage_display()})"



def _site_key():
    import secrets

    return secrets.token_urlsafe(18)


class WebsiteWidget(models.Model):
    """The hospital's website enquiry form (and, later, live chat), added to
    any website with one script tag. `site_key` is public by design (it sits
    in the page source), so it can only create enquiries: requests are
    limited to `allowed_domains`, rate-limited, and screened for bots."""

    DEFAULT_CONSENT = (
        "I agree that {hospital} may use these details to contact me about my enquiry "
        "by phone, SMS or WhatsApp, as described in its privacy notice."
    )

    hospital = models.OneToOneField("core.Hospital", on_delete=models.CASCADE, related_name="website_widget")
    site_key = models.CharField(max_length=40, unique=True, default=_site_key, editable=False)
    allowed_domains = models.JSONField(default=list, blank=True, help_text='e.g. ["swasthyam.in", "www.swasthyam.in"]. Empty = any site.')
    form_enabled = models.BooleanField(default=True)
    chat_enabled = models.BooleanField(default=False)
    title = models.CharField(max_length=120, default="Book a consultation")
    intro = models.CharField(max_length=300, blank=True, default="Leave your details and our team will call you back.")
    button_text = models.CharField(max_length=40, default="Enquire now")
    accent_color = models.CharField(max_length=9, default="#0f766e")
    services = models.JSONField(default=list, blank=True, help_text="Options for 'What do you need?'. Empty = the hospital's departments.")
    consent_text = models.TextField(blank=True, default=DEFAULT_CONSENT)
    thank_you_message = models.CharField(max_length=300, default="Thank you! Our team will call you shortly.")
    chat_welcome = models.CharField(max_length=300, default="Hi! How can we help you today?")
    chat_auto_reply = models.CharField(max_length=300, default="Thanks for your message. Someone from our team will reply here shortly. If it's urgent, please call us.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Website widget for {self.hospital_id}"

    def origin_allowed(self, origin: str) -> bool:
        if not self.allowed_domains:
            return True
        from urllib.parse import urlparse

        host = (urlparse(origin).hostname or "").lower()
        return any(host == d.lower().strip() or host.endswith("." + d.lower().strip()) for d in self.allowed_domains if d.strip())
