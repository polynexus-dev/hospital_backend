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

from apps.core.encryption import blind_index
from apps.core.fields import EncryptedCharField, EncryptedJSONField, EncryptedTextField
from apps.core.models import Hospital, TenantScopedModel
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
    # Keyed hash of the ABHA address: ABDM's discovery and consent callbacks
    # name the patient by it, and the address itself is encrypted.
    abha_address_hash = models.CharField(max_length=64, blank=True, db_index=True, editable=False)

    class Meta:
        indexes = [models.Index(fields=["hospital", "status"])]

    def __str__(self):
        return f"ABHA link for {self.patient} ({self.get_status_display()})"

    @staticmethod
    def address_hash(address):
        return blind_index(str(address or "").strip().lower())

    def save(self, *args, **kwargs):
        self.abha_address_hash = self.address_hash(self.abha_address)
        if kwargs.get("update_fields") is not None and "abha_address" in kwargs["update_fields"]:
            kwargs["update_fields"] = {*kwargs["update_fields"], "abha_address_hash"}
        super().save(*args, **kwargs)


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


# --- ABDM HIE-CM: callbacks, care contexts, consent artefacts, data flow ----
#
# M2 (this hospital as Health Information Provider) and M3 (as Health
# Information User). ABDM's flows are asynchronous: we call the gateway, and
# it answers later by calling our callback endpoints (apps.abdm.callbacks).


class ABDMFacility(TenantScopedModel):
    """The hospital's ABDM identity: its Health Facility Registry id, used as
    HIP id (and HIU id, unless registered separately). Callbacks name the
    facility in X-HIP-ID / X-HIU-ID, which is how they find their hospital."""

    hip_id = models.CharField(max_length=64, unique=True)
    hiu_id = models.CharField(max_length=64, blank=True)
    name = models.CharField(max_length=200, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["hospital"], name="abdm_one_facility_per_hospital")]

    def __str__(self):
        return f"{self.name or self.hospital} ({self.hip_id})"


class GatewayCallback(models.Model):
    """Every call ABDM's gateway made to us: the REQUEST-ID makes processing
    idempotent (the gateway retries), and the row is the audit trail of what
    was asked of this hospital and what came of it. The payload is kept
    encrypted only until it is processed — it names the patient."""

    request_id = models.CharField(max_length=100, unique=True)
    path = models.CharField(max_length=200)
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    payload = EncryptedJSONField(default=dict, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.path} {self.request_id}"


class CareContext(TenantScopedModel):
    """One of this hospital's records linked to a patient's ABHA, so the
    patient (or, with consent, another provider) can reach it. `reference`
    is what ABDM knows it by: "<kind>:<pk>" (see apps.abdm.records)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Link requested"
        LINKED = "linked", "Linked"

    class Source(models.TextChoices):
        PATIENT = "patient", "Patient (from their PHR app)"
        HOSPITAL = "hospital", "Hospital (linked at the visit)"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="abdm_care_contexts")
    abha_link = models.ForeignKey(AbhaLink, on_delete=models.CASCADE, related_name="care_contexts")
    reference = models.CharField(max_length=64)
    display = models.CharField(max_length=200)
    hi_type = models.CharField(max_length=40)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    source = models.CharField(max_length=10, choices=Source.choices)
    linked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["hospital", "reference"], name="abdm_care_context_reference_unique")]

    def __str__(self):
        return f"{self.reference} ({self.get_status_display()})"


class LinkRequest(TenantScopedModel):
    """A patient-initiated link (their PHR app asked to link care contexts
    it discovered here): we send an OTP to the patient's registered mobile
    and link only once they confirm it."""

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="+")
    abha_link = models.ForeignKey(AbhaLink, on_delete=models.CASCADE, related_name="+")
    link_reference = models.CharField(max_length=64, unique=True)
    transaction_id = models.CharField(max_length=100)
    references = models.JSONField(default=list)
    expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)


class ConsentArtefact(TenantScopedModel):
    """A consent the patient granted in their Consent Manager. As HIP: what
    another provider may pull from us, and the only thing that authorises a
    transfer. As HIU: what we may pull from others, tied to our request."""

    class Role(models.TextChoices):
        HIP = "hip", "We share (HIP)"
        HIU = "hiu", "We receive (HIU)"

    class Status(models.TextChoices):
        GRANTED = "granted", "Granted"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"
        DENIED = "denied", "Denied"

    role = models.CharField(max_length=3, choices=Role.choices)
    artefact_id = models.CharField(max_length=100)
    consent_request = models.ForeignKey(ConsentRequest, on_delete=models.CASCADE, null=True, blank=True, related_name="artefacts")
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.GRANTED)
    purpose = models.CharField(max_length=16, blank=True)
    hi_types = models.JSONField(default=list, blank=True)
    # HIP: the care-context references the patient agreed to share.
    care_contexts = models.JSONField(default=list, blank=True)
    date_from = models.DateTimeField(null=True, blank=True)
    date_to = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    counterparty_id = models.CharField(max_length=64, blank=True, help_text="The other side: HIU id when we are HIP, HIP id when we are HIU.")
    # The artefact as received (it names the patient): encrypted.
    details = EncryptedJSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["role", "artefact_id"], name="abdm_artefact_unique_per_role")]

    def __str__(self):
        return f"{self.get_role_display()} consent {self.artefact_id} ({self.get_status_display()})"

    def is_usable(self, now):
        return self.status == self.Status.GRANTED and (self.expires_at is None or self.expires_at > now)


class DataTransfer(TenantScopedModel):
    """One health-information transfer under a consent artefact, with the
    ephemeral ECDH keys for it (apps.abdm.fidelius). Our private key is
    encrypted at rest and cleared once the transfer is done."""

    class Role(models.TextChoices):
        HIP = "hip", "Sent (HIP)"
        HIU = "hiu", "Received (HIU)"

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        ACKNOWLEDGED = "acknowledged", "Acknowledged by the gateway"
        TRANSFERRED = "transferred", "Sent"
        RECEIVED = "received", "Received"
        FAILED = "failed", "Failed"

    role = models.CharField(max_length=3, choices=Role.choices)
    transaction_id = models.CharField(max_length=100, blank=True, db_index=True)
    gateway_request_id = models.CharField(max_length=100, blank=True, db_index=True)
    artefact = models.ForeignKey(ConsentArtefact, on_delete=models.CASCADE, related_name="transfers")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REQUESTED)
    date_from = models.DateTimeField(null=True, blank=True)
    date_to = models.DateTimeField(null=True, blank=True)
    private_key = EncryptedTextField(blank=True)
    public_key = models.TextField(blank=True)
    nonce = models.CharField(max_length=64, blank=True)
    data_push_url = models.URLField(max_length=500, blank=True)
    entry_count = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=500, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_role_display()} transfer {self.transaction_id or self.pk} ({self.get_status_display()})"


class ReceivedHealthRecord(TenantScopedModel):
    """A FHIR bundle another provider sent us (M3), decrypted and stored
    encrypted at rest. Read through the API like any clinical record."""

    transfer = models.ForeignKey(DataTransfer, on_delete=models.CASCADE, related_name="records")
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="abdm_received_records")
    care_context_reference = models.CharField(max_length=100, blank=True)
    title = models.CharField(max_length=200, blank=True)
    bundle = EncryptedJSONField(default=dict)
    checksum_ok = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
