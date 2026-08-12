import secrets

from django.conf import settings
from django.db import models

from apps.appointments.models import Appointment, Doctor
from apps.core.models import Department, TenantScopedModel
from apps.patients.models import Patient


def _generate_token() -> str:
    return secrets.token_urlsafe(24)


class FeedbackRequest(TenantScopedModel):
    """Post-visit feedback sent on WhatsApp in the patient's language
    (§10). `token` backs the public response link — no login required."""

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        RESPONDED = "responded", "Responded"
        EXPIRED = "expired", "Expired"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="feedback_requests")
    appointment = models.ForeignKey(Appointment, on_delete=models.SET_NULL, null=True, blank=True, related_name="feedback_requests")
    doctor = models.ForeignKey(Doctor, on_delete=models.SET_NULL, null=True, blank=True, related_name="feedback_requests")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="feedback_requests")

    token = models.CharField(max_length=64, unique=True, default=_generate_token)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SENT)
    sent_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Feedback request for {self.patient}"


class NPSResponse(TenantScopedModel):
    class Category(models.TextChoices):
        PROMOTER = "promoter", "Promoter"
        PASSIVE = "passive", "Passive"
        DETRACTOR = "detractor", "Detractor"

    feedback_request = models.OneToOneField(FeedbackRequest, on_delete=models.CASCADE, related_name="response")
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="nps_responses")
    doctor = models.ForeignKey(Doctor, on_delete=models.SET_NULL, null=True, blank=True, related_name="nps_responses")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="nps_responses")

    score = models.PositiveSmallIntegerField(help_text="0-10 NPS score.")
    category = models.CharField(max_length=16, choices=Category.choices, editable=False)
    comment = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["hospital", "category", "created_at"]),
        ]

    def __str__(self):
        return f"{self.patient} scored {self.score} ({self.category})"

    @staticmethod
    def categorize(score: int) -> str:
        if score >= 9:
            return NPSResponse.Category.PROMOTER
        if score >= 7:
            return NPSResponse.Category.PASSIVE
        return NPSResponse.Category.DETRACTOR

    def save(self, *args, **kwargs):
        self.category = self.categorize(self.score)
        super().save(*args, **kwargs)


class Complaint(TenantScopedModel):
    """Standalone complaint register (§10), independent of an NPS score —
    front desk can log a complaint directly."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        INVESTIGATING = "investigating", "Investigating"
        CLOSED = "closed", "Closed"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="complaints")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="complaints")
    description = models.TextField()
    root_cause = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="complaints")
    closed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Complaint: {self.patient} ({self.status})"


class ServiceRecoveryTask(TenantScopedModel):
    """Detractors get routed here with an SLA — before they post publicly
    (§10)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        RESOLVED = "resolved", "Resolved"

    nps_response = models.OneToOneField(NPSResponse, on_delete=models.CASCADE, related_name="recovery_task")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="service_recovery_tasks")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    sla_due_at = models.DateTimeField()
    resolution_notes = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["sla_due_at"]

    def __str__(self):
        return f"Recovery for {self.nps_response.patient} ({self.status})"
