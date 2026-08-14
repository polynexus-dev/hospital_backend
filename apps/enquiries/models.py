from django.conf import settings
from django.db import models

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

    name = models.CharField(max_length=255)
    mobile = models.CharField(max_length=20, db_index=True)
    alternate_mobile = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)

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
    service_requested = models.CharField(max_length=255, blank=True)
    urgency = models.CharField(max_length=16, choices=Urgency.choices, default=Urgency.NORMAL)

    # Manual/automated qualification score (0-100) — feeds hot/warm/cold
    # sorting on the pipeline board ahead of a scoring-rules engine.
    score = models.PositiveSmallIntegerField(default=0)

    stage = models.CharField(max_length=16, choices=Stage.choices, default=Stage.NEW)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_enquiries")

    duplicate_of = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="duplicates")

    sla_due_at = models.DateTimeField(null=True, blank=True)
    escalation_level = models.PositiveSmallIntegerField(default=0)

    lost_reason = models.CharField(max_length=32, choices=LostReason.choices, blank=True)
    lost_notes = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    estimated_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "enquiries"
        indexes = [
            models.Index(fields=["hospital", "stage", "created_at"]),
            models.Index(fields=["hospital", "mobile"]),
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
