from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.fields import EncryptedCharField, EncryptedTextField
from apps.core.managers import SoftDeleteManager
from apps.core.models import SoftDeleteModel, TenantScopedModel
from apps.patients.models import Patient


class DataRightsRequest(TenantScopedModel):
    """DPDP Act 2023 / Rules 2025 (Part A #11) — access, correction,
    erasure and nomination requests from a data principal (the patient, or
    someone verified as acting for them — see Nominee). This is the
    request/ticket record; the right itself is executed elsewhere (erasure
    calls Patient.delete(), access assembles an export via
    apps.privacy.services.collect_patient_data) and that action is logged
    back onto this row via complete(), so there's one auditable trail per
    request from submission to completion — not just a bare PATCH that
    could mark something "completed" without anything having happened."""

    class RequestType(models.TextChoices):
        ACCESS = "access", "Access my data"
        CORRECTION = "correction", "Correct my data"
        ERASURE = "erasure", "Erase my data"
        NOMINATION = "nomination", "Nominate someone to act for me"

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        VERIFIED = "verified", "Identity verified"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"

    class Channel(models.TextChoices):
        PHONE = "phone", "Phone"
        EMAIL = "email", "Email"
        WRITTEN = "written", "Written / in-person"
        PORTAL = "portal", "Patient portal"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="data_rights_requests")
    request_type = models.CharField(max_length=16, choices=RequestType.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.SUBMITTED)
    channel = models.CharField(max_length=16, choices=Channel.choices, default=Channel.PHONE)
    # What's being requested/corrected to, in the requester's own words —
    # encrypted since a correction request routinely repeats the sensitive
    # value itself (e.g. "my phone number should be ...").
    details = EncryptedTextField(blank=True)

    submitted_at = models.DateTimeField(auto_now_add=True)
    sla_due_at = models.DateTimeField(editable=False)

    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    resolution_notes = EncryptedTextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [
            models.Index(fields=["hospital", "status"]),
        ]

    def save(self, *args, **kwargs):
        if not self.sla_due_at:
            self.sla_due_at = timezone.now() + timedelta(days=settings.DATA_RIGHTS_REQUEST_SLA_DAYS)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_request_type_display()} request for {self.patient} ({self.get_status_display()})"


class GrievanceTicket(TenantScopedModel):
    """DPDP Act 2023 §2.5 — "Grievance officer named, contactable, with
    SLA". Broader than DataRightsRequest: a grievance can be about
    anything (care quality, billing, staff conduct), not only a specific
    data-rights action, and routes to the hospital's designated grievance
    officer (core.Hospital.grievance_officer_*)."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        RESOLVED = "resolved", "Resolved"
        ESCALATED = "escalated", "Escalated"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    patient = models.ForeignKey(Patient, on_delete=models.SET_NULL, null=True, blank=True, related_name="grievance_tickets")
    subject = models.CharField(max_length=255)
    description = EncryptedTextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=16, choices=Priority.choices, default=Priority.NORMAL)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    submitted_at = models.DateTimeField(auto_now_add=True)
    sla_due_at = models.DateTimeField(editable=False)
    resolution = EncryptedTextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at"]
        indexes = [
            models.Index(fields=["hospital", "status"]),
        ]

    def save(self, *args, **kwargs):
        if not self.sla_due_at:
            self.sla_due_at = timezone.now() + timedelta(days=settings.GRIEVANCE_SLA_DAYS)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Grievance: {self.subject} ({self.get_status_display()})"


class Nominee(TenantScopedModel, SoftDeleteModel):
    """DPDP Act 2023 §14 — a data principal may nominate another individual
    to exercise their rights in the event of death or incapacity. A
    patient can accumulate more than one historical nominee row
    (soft-deleted when superseded, per SoftDeleteModel) — `is_active`
    marks whichever one currently applies."""

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="nominees")
    name = models.CharField(max_length=150)
    relationship = models.CharField(max_length=100, help_text="e.g. spouse, child, parent")
    phone = EncryptedCharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    objects = SoftDeleteManager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.relationship}) for {self.patient}"
