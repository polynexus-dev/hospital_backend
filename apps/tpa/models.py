from decimal import Decimal

from django.db import models
from django.utils import timezone
from apps.core.fields import EncryptedTextField
from apps.core.encryption import compute_blind_index
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
    # NHCX participant code of the payer (from the NHCX registry).
    nhcx_participant_code = models.CharField(max_length=100, blank=True)
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

    policy_number = EncryptedTextField()
    policy_number_lookup = models.CharField(max_length=64, db_index=True, editable=False, blank=True)
    claim_amount = models.DecimalField(max_digits=12, decimal_places=2)
    approved_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.SUBMITTED)

    checklist = models.JSONField(default=dict, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)

    # Pre-auth form (what the insurer's / NHCX pre-authorisation needs).
    member_id = models.CharField(max_length=80, blank=True, help_text="Insurer member / card ID")
    admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="preauth_requests")
    treating_doctor = models.ForeignKey("appointments.Doctor", on_delete=models.SET_NULL, null=True, blank=True, related_name="preauth_requests")
    admission_type = models.CharField(max_length=10, default="planned", help_text="planned | emergency")
    expected_admission_date = models.DateField(null=True, blank=True)
    expected_stay_days = models.PositiveSmallIntegerField(null=True, blank=True)
    room_category = models.CharField(max_length=16, blank=True, help_text="general | semi_private | private | icu ...")
    diagnosis_code = models.CharField(max_length=12, blank=True, help_text="ICD-10")
    diagnosis = models.CharField(max_length=255, blank=True)
    procedure_name = models.CharField(max_length=255, blank=True)
    procedure_code = models.CharField(max_length=30, blank=True, help_text="e.g. PM-JAY / insurer procedure code")
    clinical_summary = models.TextField(blank=True, help_text="Presenting complaints, findings, line of treatment")
    cost_breakdown = models.JSONField(default=list, blank=True, help_text='[{"category": "room", "description": "...", "amount": 12000}]')

    class Meta:
        ordering = ["-submitted_at"]

    def save(self, *args, **kwargs):
        self.policy_number_lookup = compute_blind_index(self.policy_number) if self.policy_number else ""
        super().save(*args, **kwargs)

    def __str__(self):
        return f"PreAuth #{self.id}: {self.patient.full_name} ({self.tpa_company.name})"


class Claim(TenantScopedModel):
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


class NonPayableList(TenantScopedModel):
    """Items an insurer won't pay for (e.g. the IRDAI list of non-payable
    items, or an insurer's own list). No insurer = applies to every insurer.
    Matching bill lines are charged to the patient, not the claim."""

    name = models.CharField(max_length=150)
    tpa_company = models.ForeignKey(TPACompany, on_delete=models.CASCADE, null=True, blank=True, related_name="non_payable_lists",
                                    help_text="Blank = applies to every insurer / TPA.")
    applies_to_categories = models.JSONField(default=list, blank=True, help_text='Bill patient categories, e.g. ["insurance", "corporate"]; empty = insurance only.')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def categories(self):
        return self.applies_to_categories or ["insurance"]


class NonPayableItem(TenantScopedModel):
    """One entry: matched to bill lines by service tariff, by medicine name,
    or by a word in the line's description."""

    class Group(models.TextChoices):
        NOT_PAYABLE = "list_1", "List I — not payable"
        ROOM = "list_2", "List II — part of room charges"
        PROCEDURE = "list_3", "List III — part of procedure charges"
        TREATMENT = "list_4", "List IV — part of treatment costs"
        OTHER = "other", "Insurer-specific"

    non_payable_list = models.ForeignKey(NonPayableList, on_delete=models.CASCADE, related_name="items")
    group = models.CharField(max_length=8, choices=Group.choices, default=Group.NOT_PAYABLE)
    tariff = models.ForeignKey("finance.ServiceTariff", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    medicine = models.ForeignKey("pharmacy.Medicine", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    keyword = models.CharField(max_length=100, blank=True, help_text="Case-insensitive; matches bill lines whose description contains it.")
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["group", "keyword"]

    def label(self):
        what = self.tariff.name if self.tariff_id else self.medicine.name if self.medicine_id else self.keyword
        return f"{what} ({self.get_group_display()})"


class NHCXProfile(TenantScopedModel):
    """The hospital's NHCX identity: its participant code on the exchange
    and the registry IDs a claim carries."""

    participant_code = models.CharField(max_length=100, blank=True, help_text="Provider participant code from the NHCX registry")
    hfr_id = models.CharField(max_length=40, blank=True, help_text="ABDM Health Facility Registry ID")
    rohini_id = models.CharField(max_length=20, blank=True, help_text="ROHINI ID (IIB hospital registry)")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["hospital"], name="one_nhcx_profile_per_hospital")]


class ClaimMessage(TenantScopedModel):
    """Insurance inbox: one piece of correspondence on a pre-auth or claim —
    an insurer query, our reply, an approval / enhancement / rejection, a
    settlement advice or a note. Incoming messages move the case along
    (apps.tpa.inbox.record)."""

    class Direction(models.TextChoices):
        INCOMING = "in", "From insurer / TPA"
        OUTGOING = "out", "From hospital"

    class Kind(models.TextChoices):
        QUERY = "query", "Query"
        DOCUMENTS = "documents", "Documents requested"
        REPLY = "reply", "Reply to query"
        APPROVAL = "approval", "Approval"
        ENHANCEMENT_REQUEST = "enhancement", "Enhancement request"
        ENHANCEMENT_APPROVAL = "enhancement_ok", "Enhancement approved"
        REJECTION = "rejection", "Rejection"
        SETTLEMENT = "settlement", "Settlement advice"
        NOTE = "note", "Note"

    preauth_request = models.ForeignKey(PreAuthRequest, on_delete=models.CASCADE, null=True, blank=True, related_name="messages")
    claim = models.ForeignKey(Claim, on_delete=models.CASCADE, null=True, blank=True, related_name="messages")
    direction = models.CharField(max_length=3, choices=Direction.choices)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    subject = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Approved / enhanced / settled amount")
    attachment = models.FileField(upload_to="tpa/messages/", blank=True)
    respond_by = models.DateField(null=True, blank=True, help_text="Queries / document requests: reply due by")
    in_reply_to = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replies")
    resolved_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    received_at = models.DateTimeField(default=timezone.now)

    class Source(models.TextChoices):
        MANUAL = "manual", "Logged by staff"
        EMAIL = "email", "Imported email"

    source = models.CharField(max_length=6, choices=Source.choices, default=Source.MANUAL)
    email_from = models.EmailField(blank=True)
    email_message_id = models.CharField(max_length=255, blank=True, db_index=True)
    # Imported emails that move money / close a case, or that matched no
    # case, wait here until someone confirms them (apps.tpa.mailbox).
    needs_review = models.BooleanField(default=False)

    class Meta:
        ordering = ["-received_at", "-id"]
        indexes = [models.Index(fields=["hospital", "direction", "kind", "resolved_at"])]

    @property
    def is_open(self):
        return self.direction == self.Direction.INCOMING and self.kind in (self.Kind.QUERY, self.Kind.DOCUMENTS) and self.resolved_at is None


class TPAMailbox(TenantScopedModel):
    """The mailbox insurers / TPAs write to; polled over IMAP into the insurance inbox."""

    host = models.CharField(max_length=200, help_text="e.g. imap.gmail.com")
    port = models.PositiveIntegerField(default=993)
    use_ssl = models.BooleanField(default=True)
    username = models.CharField(max_length=200)
    password = EncryptedTextField(help_text="App password for the mailbox")
    folder = models.CharField(max_length=100, default="INBOX")
    is_active = models.BooleanField(default=True)
    last_uid = models.PositiveBigIntegerField(default=0, editable=False)
    last_checked_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_error = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["hospital"], name="one_tpa_mailbox_per_hospital")]
