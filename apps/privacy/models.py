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


class ConsentPurpose(models.TextChoices):
    """Why a patient's personal data is processed (DPDP Act 2023 §6: consent
    is given per specified purpose, and can be withdrawn as easily)."""

    TREATMENT = "treatment", "Treatment and care"
    BILLING_INSURANCE = "billing_insurance", "Billing, insurance and government schemes"
    REMINDERS = "reminders", "Appointment and report reminders"
    MARKETING = "marketing", "Health camps, offers and newsletters"
    RESEARCH = "research", "Research and clinical trials (de-identified)"
    ABDM_SHARING = "abdm_sharing", "Sharing records through ABDM"
    TELEMEDICINE = "telemedicine", "Telemedicine consultations"


class ConsentNotice(TenantScopedModel):
    """The notice shown to a patient for one purpose (DPDP Act §5, Rules
    2025): what data, why, for how long, and how to withdraw or complain.
    Published text never changes — a change is a new version — so the exact
    words a patient agreed to can always be shown."""

    class Language(models.TextChoices):
        ENGLISH = "en", "English"
        HINDI = "hi", "Hindi"
        MARATHI = "mr", "Marathi"

    purpose = models.CharField(max_length=24, choices=ConsentPurpose.choices)
    language = models.CharField(max_length=4, choices=Language.choices, default=Language.ENGLISH)
    version = models.PositiveIntegerField(editable=False)
    title = models.CharField(max_length=200)
    body = models.TextField(help_text="What data is collected, why, how long it is kept, and how to withdraw or complain.")
    is_active = models.BooleanField(default=True, help_text="The version shown to patients now. Publishing a new version retires the old one.")
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["purpose", "language", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["hospital", "purpose", "language", "version"], name="unique_consent_notice_version"),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding:
            latest = ConsentNotice.objects.filter(hospital_id=self.hospital_id, purpose=self.purpose, language=self.language).order_by("-version").first()
            self.version = (latest.version + 1) if latest else 1
            if self.is_active:
                ConsentNotice.objects.filter(hospital_id=self.hospital_id, purpose=self.purpose, language=self.language).update(is_active=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_purpose_display()} v{self.version} ({self.language})"


class PatientConsent(TenantScopedModel):
    """A patient's answer to one consent notice, and its withdrawal. Rows
    are never edited after the fact: withdrawing stamps the withdrawal on
    the same row, and a new consent is a new row."""

    class Status(models.TextChoices):
        GRANTED = "granted", "Granted"
        DECLINED = "declined", "Declined"
        WITHDRAWN = "withdrawn", "Withdrawn"

    class Method(models.TextChoices):
        WRITTEN = "written", "Signed form"
        DIGITAL = "digital", "Digital (tablet/OTP)"
        VERBAL = "verbal", "Verbal, witnessed by staff"
        PORTAL = "portal", "Patient portal"

    class GivenBy(models.TextChoices):
        SELF = "self", "The patient"
        GUARDIAN = "guardian", "Parent or lawful guardian"
        NOMINEE = "nominee", "Nominee"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="dpdp_consents")
    notice = models.ForeignKey(ConsentNotice, on_delete=models.PROTECT, related_name="consents")
    purpose = models.CharField(max_length=24, choices=ConsentPurpose.choices, editable=False)
    status = models.CharField(max_length=12, choices=Status.choices)
    method = models.CharField(max_length=12, choices=Method.choices, default=Method.WRITTEN)
    given_by = models.CharField(max_length=12, choices=GivenBy.choices, default=GivenBy.SELF)
    guardian_name = models.CharField(max_length=150, blank=True)
    captured_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    withdrawn_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    withdrawal_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        self.purpose = self.notice.purpose
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Consent {self.purpose} for patient {self.patient_id}: {self.status}"
