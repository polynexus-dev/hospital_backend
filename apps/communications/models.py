from django.conf import settings
from django.db import models

from apps.core.models import TenantScopedModel
from apps.patients.models import Patient


class Channel(models.TextChoices):
    WHATSAPP = "whatsapp", "WhatsApp"
    SMS = "sms", "SMS"
    EMAIL = "email", "Email"
    CALL_NOTE = "call_note", "Call note"


class Template(TenantScopedModel):
    """Multi-language template library (§5). `purpose` is a free-text code
    other apps look templates up by, e.g. appointment_reminder_24h,
    appointment_reminder_2h, feedback_request, callback_ack."""

    class Language(models.TextChoices):
        MARATHI = "mr", "Marathi"
        HINDI = "hi", "Hindi"
        ENGLISH = "en", "English"

    name = models.CharField(max_length=150)
    purpose = models.CharField(max_length=100, db_index=True)
    channel = models.CharField(max_length=16, choices=Channel.choices)
    language = models.CharField(max_length=8, choices=Language.choices)
    subject = models.CharField(max_length=255, blank=True, help_text="Email subject / WhatsApp template header.")
    body = models.TextField(help_text="Use {variable} placeholders, e.g. {patient_name}, {doctor_name}, {date}, {time}.")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["hospital", "purpose", "channel", "language"], name="unique_template_per_purpose_channel_language"),
        ]

    def __str__(self):
        return f"{self.name} [{self.channel}/{self.language}]"

    def render(self, context: dict) -> str:
        return self.body.format(**context)


class ConsentOptOut(TenantScopedModel):
    """Per-channel, per-purpose consent/opt-out ledger (§5, and feeds the
    DPDP consent record requirement in §13)."""

    class Purpose(models.TextChoices):
        TRANSACTIONAL = "transactional", "Transactional"
        MARKETING = "marketing", "Marketing"
        ALL = "all", "All communication"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="consent_records")
    channel = models.CharField(max_length=16, choices=Channel.choices)
    purpose = models.CharField(max_length=16, choices=Purpose.choices, default=Purpose.ALL)
    is_opted_out = models.BooleanField(default=False)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["patient", "channel", "purpose"], name="unique_consent_per_patient_channel_purpose"),
        ]

    def __str__(self):
        return f"{self.patient} {self.channel}/{self.purpose}: {'opted out' if self.is_opted_out else 'opted in'}"


class Message(TenantScopedModel):
    """Unified inbox row — one thread per patient across every channel
    (§5)."""

    class Direction(models.TextChoices):
        INBOUND = "inbound", "Inbound"
        OUTBOUND = "outbound", "Outbound"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        READ = "read", "Read"
        FAILED = "failed", "Failed"
        RECEIVED = "received", "Received"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="messages")
    channel = models.CharField(max_length=16, choices=Channel.choices)
    direction = models.CharField(max_length=16, choices=Direction.choices)
    template = models.ForeignKey(Template, on_delete=models.SET_NULL, null=True, blank=True, related_name="messages")

    body = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    is_read = models.BooleanField(default=False, help_text="Whether staff has viewed this inbound message (drives per-thread unread counts).")

    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    provider_message_id = models.CharField(max_length=128, blank=True, db_index=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["hospital", "patient", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.get_channel_display()} {self.get_direction_display()} to {self.patient}"


class Thread(TenantScopedModel):
    """One thread per patient per channel — groups the flat `Message` log
    for the Inbox screen (unread counts, owner, SLA timer)."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="message_threads")
    channel = models.CharField(max_length=16, choices=Channel.choices)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="message_threads")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_inbound_at = models.DateTimeField(null=True, blank=True)
    sla_due_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["hospital", "patient", "channel"], name="unique_thread_per_patient_channel"),
        ]
        ordering = ["-last_message_at"]

    def __str__(self):
        return f"{self.patient} {self.get_channel_display()} thread"
