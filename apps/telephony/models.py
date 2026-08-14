from django.conf import settings
from django.db import models

from apps.core.models import Department, TenantScopedModel
from apps.enquiries.models import Enquiry
from apps.patients.models import Patient


class IVRRoute(TenantScopedModel):
    """Department / language routing rule for inbound IVR (§1)."""

    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="ivr_routes")
    language = models.CharField(max_length=8, default="mr")
    dial_in_number = models.CharField(max_length=32, blank=True)
    ivr_option_code = models.CharField(max_length=16, blank=True, help_text="Key pressed / intent code from the IVR provider.")
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.department.name} ({self.language})"


class Call(TenantScopedModel):
    class Direction(models.TextChoices):
        INBOUND = "inbound", "Inbound"
        OUTBOUND = "outbound", "Outbound"

    class Status(models.TextChoices):
        ANSWERED = "answered", "Answered"
        MISSED = "missed", "Missed"
        RNR = "rnr", "Ring no response"
        BUSY = "busy", "Busy"
        FAILED = "failed", "Failed"
        VOICEMAIL = "voicemail", "Voicemail"

    class CallReason(models.TextChoices):
        """P1 stores this only if a human sets it; P2 auto-classifies from
        the transcript (Part A §1)."""
        OPD = "opd", "OPD"
        SURGICAL = "surgical", "Surgical"
        SECOND_OPINION = "second_opinion", "Second opinion"
        BILLING = "billing", "Billing"
        REPORT = "report", "Report"
        COMPLAINT = "complaint", "Complaint"
        OTHER = "other", "Other"

    direction = models.CharField(max_length=8, choices=Direction.choices)
    status = models.CharField(max_length=16, choices=Status.choices)

    from_number = models.CharField(max_length=32, db_index=True)
    to_number = models.CharField(max_length=32, blank=True)

    patient = models.ForeignKey(Patient, on_delete=models.SET_NULL, null=True, blank=True, related_name="calls")
    # Pre-conversion lead this call belongs to — lets the enquiry pipeline
    # show call history for a caller who hasn't become a Patient yet.
    enquiry = models.ForeignKey(Enquiry, on_delete=models.SET_NULL, null=True, blank=True, related_name="calls")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="calls")
    operator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="handled_calls")

    started_at = models.DateTimeField()
    answered_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)

    recording_url = models.URLField(blank=True)
    consent_recorded = models.BooleanField(default=False)

    ivr_path = models.CharField(max_length=255, blank=True, help_text="Department/language/option breadcrumb the caller went through before this call was logged.")

    call_reason = models.CharField(max_length=32, choices=CallReason.choices, blank=True)
    notes = models.TextField(blank=True)

    provider_name = models.CharField(max_length=64, blank=True)
    provider_call_id = models.CharField(max_length=128, blank=True, db_index=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["hospital", "status", "started_at"]),
            models.Index(fields=["hospital", "from_number"]),
        ]

    def __str__(self):
        return f"{self.get_direction_display()} {self.from_number} -> {self.to_number} ({self.status})"


class CallbackTask(TenantScopedModel):
    """Missed-call callback queue and RNR chase list, with owner + SLA +
    escalation (§1)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        DONE = "done", "Done"
        ESCALATED = "escalated", "Escalated"

    call = models.ForeignKey(Call, on_delete=models.SET_NULL, null=True, blank=True, related_name="callback_tasks")
    patient = models.ForeignKey(Patient, on_delete=models.SET_NULL, null=True, blank=True, related_name="callback_tasks")
    phone_number = models.CharField(max_length=32)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="callback_tasks")

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="callback_tasks")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    sla_due_at = models.DateTimeField()
    escalation_level = models.PositiveSmallIntegerField(default=0)
    attempt_count = models.PositiveSmallIntegerField(default=0)

    notes = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["sla_due_at"]
        indexes = [
            models.Index(fields=["hospital", "status", "sla_due_at"]),
        ]

    def __str__(self):
        return f"Callback {self.phone_number} ({self.status})"
