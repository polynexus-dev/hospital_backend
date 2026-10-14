import uuid

from django.conf import settings
from django.db import models

from apps.core.fields import EncryptedTextField
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

    # AI analysis of the recording (apps.telephony.call_ai): self-hosted
    # Whisper transcribes, the hospital's Ollama model summarises. For staff
    # only — nothing here is ever shown to the caller.
    class AIStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    class LeadClass(models.TextChoices):
        HOT = "hot", "Hot"
        WARM = "warm", "Warm"
        COLD = "cold", "Cold"
        NOT_A_LEAD = "not_a_lead", "Not a lead"

    class MissedOpportunity(models.TextChoices):
        UNRETURNED = "unreturned", "Missed call never returned"
        NOT_BOOKED = "not_booked", "Wanted to book, no booking made"

    transcript = EncryptedTextField(blank=True)
    ai_summary = EncryptedTextField(blank=True)
    ai_lead_class = models.CharField(max_length=16, choices=LeadClass.choices, blank=True)
    ai_booking_intent = models.BooleanField(null=True, blank=True)
    ai_status = models.CharField(max_length=16, choices=AIStatus.choices, blank=True)
    ai_error = models.CharField(max_length=255, blank=True)
    ai_processed_at = models.DateTimeField(null=True, blank=True)
    missed_opportunity = models.CharField(max_length=16, choices=MissedOpportunity.choices, blank=True, db_index=True)
    missed_opportunity_at = models.DateTimeField(null=True, blank=True)

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

    class Source(models.TextChoices):
        MISSED_CALL = "missed_call", "Missed call"
        QUEUE = "queue", "Caller asked from the queue"
        IVR_MENU = "ivr_menu", "Caller asked from the phone menu"
        MANUAL = "manual", "Raised by staff"

    call = models.ForeignKey(Call, on_delete=models.SET_NULL, null=True, blank=True, related_name="callback_tasks")
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
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


class IVRMenu(TenantScopedModel):
    """The phone menu a caller hears, built in our app (Settings > Phone
    menu) and served to the phone system step by step through the dynamic
    IVR webhook in apps.telephony.ivr. Any PBX that can call a URL from its
    call flow can use it; the menu itself never lives in the PBX."""

    name = models.CharField(max_length=120)
    dial_in_number = models.CharField(max_length=32, blank=True, help_text="The DID callers dial. Informational.")
    webhook_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    is_active = models.BooleanField(default=True)

    fallback_target = models.CharField(max_length=64, blank=True, help_text="Queue / extension to send callers to after repeated wrong keys. Empty: hang up politely.")
    # Hold menu (callers waiting in a queue): position announcements plus
    # keys for a callback, an information message and a booking link.
    hold_announce_seconds = models.PositiveSmallIntegerField(default=45)
    hold_info_text = models.TextField(blank=True, help_text="Read out when a waiting caller presses 2, e.g. OPD timings.")
    booking_url = models.URLField(blank=True, help_text="Sent by WhatsApp/SMS when a waiting caller presses 3.")

    def __str__(self):
        return self.name


class IVRNode(TenantScopedModel):
    """One option in the phone menu tree. The root (no parent) is the
    welcome menu; a caller pressing `digit` at the parent selects this node,
    which then does `action`."""

    class Action(models.TextChoices):
        MENU = "menu", "Play a sub-menu"
        LANGUAGE = "language", "Choose language, then a sub-menu"
        QUEUE = "queue", "Connect to a department's queue"
        INFO = "info", "Read a message, then repeat the menu"
        CALLBACK = "callback", "Book a callback and hang up"
        BOOKING_LINK = "booking_link", "Send the booking link"

    menu = models.ForeignKey(IVRMenu, on_delete=models.CASCADE, related_name="nodes")
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="children")
    digit = models.CharField(max_length=1, blank=True, help_text="Key pressed at the parent menu. Empty for the root.")
    label = models.CharField(max_length=120)
    action = models.CharField(max_length=16, choices=Action.choices, default=Action.MENU)
    prompt = models.TextField(blank=True, help_text="What the caller hears: the menu options, or the message for an info option.")
    language = models.CharField(max_length=8, blank=True, help_text="For a language option: en / hi / mr.")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="ivr_nodes")
    queue_target = models.CharField(max_length=64, blank=True, help_text="PBX queue / ring group / extension for a queue option.")
    queue_agents = models.PositiveSmallIntegerField(default=1, help_text="Staff answering this queue at once; used to estimate the wait.")
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "digit"]
        constraints = [
            models.UniqueConstraint(fields=["parent", "digit"], name="unique_ivr_digit_per_parent"),
        ]

    def __str__(self):
        return f"{self.digit or 'root'}: {self.label}"


class IVRSession(TenantScopedModel):
    """One caller's live journey through the menu and the queue, keyed by
    the PBX's call id. It is what queue positions are counted from, and when
    the PBX later posts the finished call, its path and department are
    copied onto the Call."""

    class State(models.TextChoices):
        IN_MENU = "in_menu", "In the menu"
        QUEUED = "queued", "Waiting in queue"
        ANSWERED = "answered", "Answered"
        CALLBACK = "callback", "Asked for a callback"
        ABANDONED = "abandoned", "Hung up"
        ENDED = "ended", "Ended in the menu"

    menu = models.ForeignKey(IVRMenu, on_delete=models.CASCADE, related_name="sessions")
    provider_call_id = models.CharField(max_length=128)
    caller = models.CharField(max_length=32)
    is_test = models.BooleanField(default=False, help_text="Started from the in-app simulator; never counted in the live queue.")

    state = models.CharField(max_length=16, choices=State.choices, default=State.IN_MENU)
    current_node = models.ForeignKey(IVRNode, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    language = models.CharField(max_length=8, blank=True)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    queue_node = models.ForeignKey(IVRNode, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    path = models.CharField(max_length=255, blank=True)
    wrong_keys = models.PositiveSmallIntegerField(default=0)

    queued_at = models.DateTimeField(null=True, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    call = models.ForeignKey(Call, on_delete=models.SET_NULL, null=True, blank=True, related_name="ivr_sessions")

    class Meta:
        ordering = ["queued_at", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["hospital", "provider_call_id"], name="unique_ivr_session_per_call"),
        ]
        indexes = [
            models.Index(fields=["hospital", "state", "queued_at"]),
        ]

    def __str__(self):
        return f"{self.caller} ({self.state})"
