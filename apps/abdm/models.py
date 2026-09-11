"""
Data model for India's Ayushman Bharat Digital Mission (ABDM) — ABHA
(health ID) linking and the HIE-CM consent-to-share flow — plus a light
NHCX (National Health Claims Exchange) transaction log bridging to
apps.tpa's existing pre-authorization workflow.

This app is the *scaffolding*: the models, state machines, and audit
trail that a real ABDM/NHCX integration needs regardless of which gateway
implementation ends up calling the actual government APIs. The gateway
call itself is intentionally not implemented yet (see gateway.py) — every
row here is written by application code reacting to a gateway response,
so the shape doesn't change once real credentials exist; only
`ABDM_GATEWAY`/`NHCX_GATEWAY` env vars and a new StubABDMGateway-shaped
class do.
"""
from django.conf import settings
from django.db import models

from apps.core.fields import EncryptedCharField
from apps.core.models import TenantScopedModel
from apps.patients.models import Patient


class AbhaLink(TenantScopedModel):
    """One patient's ABHA (Ayushman Bharat Health Account) linkage state.

    A patient has at most one ABHA — modeled as a OneToOne rather than a
    FK — but can go through PENDING (OTP sent, not yet confirmed) more
    than once if a first attempt fails/expires, which is why history
    isn't modeled here at all: only the current linkage state matters,
    the OTP transaction itself is ABDM's concern, not ours to retain.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Verification in progress"
        LINKED = "linked", "Linked"
        FAILED = "failed", "Verification failed"
        REVOKED = "revoked", "Revoked"

    class VerificationMethod(models.TextChoices):
        AADHAAR_OTP = "aadhaar_otp", "Aadhaar OTP"
        MOBILE_OTP = "mobile_otp", "Mobile OTP"
        EXISTING_ABHA = "existing_abha", "Existing ABHA number/address"

    patient = models.OneToOneField(Patient, on_delete=models.CASCADE, related_name="abha_link")

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    verification_method = models.CharField(max_length=16, choices=VerificationMethod.choices)

    # Populated only once status=LINKED — encrypted, this is a national
    # health identifier (Part A #2's ABHA callout in apps.core.encryption
    # was written in anticipation of exactly this field).
    abha_number = EncryptedCharField(max_length=32, blank=True, help_text="14-digit ABHA number, e.g. 12-3456-7890-1234")
    abha_address = EncryptedCharField(max_length=64, blank=True, help_text="ABHA address / PHR address, e.g. jane.doe@abdm")

    # Correlation id from the gateway's OTP transaction — not a secret
    # (it identifies a transaction, not a credential), kept to let a
    # retry/status-check reference the same in-flight verification.
    gateway_txn_id = models.CharField(max_length=100, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)

    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    initiated_at = models.DateTimeField(auto_now_add=True)
    linked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["hospital", "status"])]

    def __str__(self):
        return f"ABHA link for {self.patient} ({self.get_status_display()})"


class ConsentRequest(TenantScopedModel):
    """One HIE-CM consent-to-share request/grant/expiry cycle (ABDM's
    consent-manager flow) — a hospital (as Health Information User) asks
    to view a slice of a patient's health records held elsewhere, the
    patient's chosen Consent Manager app relays the request to them, and
    grants/denies it out-of-band. This row tracks that lifecycle; it
    never itself contains clinical data — see HealthRecordFetch for what
    was actually retrieved once granted.
    """

    class Purpose(models.TextChoices):
        # ABDM's standard consent purpose codes (a subset — the ones
        # relevant to a hospital's own care/billing use, not the full
        # HL7 PurposeOfUse value set).
        CARE_MANAGEMENT = "CAREMGT", "Care Management"
        BREAK_THE_GLASS = "BTG", "Break the Glass"
        PUBLIC_HEALTH = "PUBHLTH", "Public Health"
        INSURANCE = "INSPMT", "Insurance / Claim Settlement"

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        GRANTED = "granted", "Granted"
        DENIED = "denied", "Denied"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="abdm_consent_requests")
    abha_link = models.ForeignKey(AbhaLink, on_delete=models.CASCADE, related_name="consent_requests")

    purpose = models.CharField(max_length=16, choices=Purpose.choices, default=Purpose.CARE_MANAGEMENT)
    # ABDM "HI types" — e.g. ["OPConsultation", "Prescription", "DiagnosticReport"].
    hi_types = models.JSONField(default=list, blank=True)
    date_range_from = models.DateField()
    date_range_to = models.DateField()

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REQUESTED)
    # ABDM's own identifiers for this request/artifact — correlation ids,
    # not secrets; the actual authorization lives with ABDM/the patient's
    # Consent Manager, not in a token this app could replay on its own.
    gateway_request_id = models.CharField(max_length=100, blank=True)
    consent_artifact_id = models.CharField(max_length=100, blank=True)

    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    requested_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [models.Index(fields=["hospital", "status"])]

    def __str__(self):
        return f"Consent request for {self.patient} ({self.get_status_display()})"


class HealthRecordFetch(TenantScopedModel):
    """Audit row for one actual retrieval of a patient's external health
    records under a GRANTED consent artifact — mirrors apps.core.models.
    EmergencyAccessLog's philosophy: a row here means data was genuinely
    pulled, not merely that a consent existed. The records themselves are
    not stored here — the fetch response is handed back to the caller
    (see views.ConsentRequestViewSet.fetch_records) and this only logs
    that it happened, by whom, and how much."""

    consent_request = models.ForeignKey(ConsentRequest, on_delete=models.CASCADE, related_name="fetches")
    fetched_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    fetched_at = models.DateTimeField(auto_now_add=True)
    hi_types_fetched = models.JSONField(default=list, blank=True)
    record_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-fetched_at"]

    def __str__(self):
        return f"Health record fetch for consent #{self.consent_request_id} at {self.fetched_at}"


class NHCXTransaction(TenantScopedModel):
    """One exchange with the National Health Claims Exchange (NHCX) —
    the standardized government claims-exchange gateway that
    apps.tpa.PreAuthRequest's manual/insurer-specific workflow is the
    non-standardized equivalent of today. Deliberately a thin log
    alongside PreAuthRequest rather than a replacement for it: the
    clinical/financial substance of a claim still lives on PreAuthRequest/
    Claim (apps.tpa.models) — this only tracks the exchange's own
    protocol-level state (submitted/pending/approved/rejected via NHCX,
    as opposed to the hospital's internal TAT tracking)."""

    class TransactionType(models.TextChoices):
        ELIGIBILITY_CHECK = "eligibility_check", "Coverage Eligibility Check"
        PRE_AUTH = "pre_auth", "Pre-Authorization"
        CLAIM = "claim", "Claim"

    class Status(models.TextChoices):
        INITIATED = "initiated", "Initiated"
        PENDING = "pending", "Pending at payer"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        ERROR = "error", "Gateway error"

    preauth_request = models.ForeignKey(
        "tpa.PreAuthRequest", on_delete=models.SET_NULL, null=True, blank=True, related_name="nhcx_transactions",
    )
    transaction_type = models.CharField(max_length=20, choices=TransactionType.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.INITIATED)

    nhcx_transaction_id = models.CharField(max_length=100, blank=True)
    gateway_response_summary = models.CharField(max_length=255, blank=True)

    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    initiated_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-initiated_at"]
        indexes = [models.Index(fields=["hospital", "status"])]

    def __str__(self):
        return f"NHCX {self.get_transaction_type_display()} #{self.id} ({self.get_status_display()})"
