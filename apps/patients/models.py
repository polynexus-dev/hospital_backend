from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models

from apps.core.encryption import EncryptedTextField
from apps.core.models import TenantScopedModel

# Patient documents (reports, prescriptions, ID proofs, consent forms) are
# clinical/PII records — restrict uploads to the file types this app
# actually needs to display/preview, and cap size so a single upload can't
# exhaust disk on the shared media volume.
ALLOWED_DOCUMENT_EXTENSIONS = ["pdf", "jpg", "jpeg", "png", "doc", "docx"]
MAX_DOCUMENT_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


def validate_document_size(file):
    if file.size > MAX_DOCUMENT_SIZE_BYTES:
        raise ValidationError(
            f"File is too large ({file.size / (1024 * 1024):.1f} MB) — "
            f"maximum allowed size is {MAX_DOCUMENT_SIZE_BYTES // (1024 * 1024)} MB."
        )


class Patient(TenantScopedModel):
    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"
        OTHER = "other", "Other"
        UNDISCLOSED = "undisclosed", "Prefer not to say"

    class PreferredLanguage(models.TextChoices):
        MARATHI = "mr", "Marathi"
        HINDI = "hi", "Hindi"
        ENGLISH = "en", "English"

    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=16, choices=Gender.choices, blank=True)

    mobile = models.CharField(max_length=20, db_index=True)
    alternate_mobile = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)

    address = models.TextField(blank=True)
    city = models.CharField(max_length=120, blank=True)

    national_id_type = models.CharField(max_length=32, blank=True, help_text="e.g. Aadhaar, PAN, Passport")
    # Encrypted at rest (apps.core.encryption) — Aadhaar/PAN/Passport
    # numbers are sensitive personal data under the DPDP Act / SPDI Rules.
    # Not filterable/searchable as a result — never add this to a
    # filterset_fields/search_fields list.
    national_id_number = EncryptedTextField(blank=True)

    insurance_provider = models.CharField(max_length=150, blank=True)
    # Encrypted at rest — same reasoning as national_id_number above.
    insurance_policy_number = EncryptedTextField(blank=True)
    employer = models.CharField(max_length=150, blank=True)

    attendant_name = models.CharField(max_length=150, blank=True)
    attendant_phone = models.CharField(max_length=20, blank=True)
    attendant_relation = models.CharField(max_length=50, blank=True, help_text="e.g. daughter, spouse")
    referring_doctor_name = models.CharField(
        max_length=150,
        blank=True,
        help_text="External referring doctor, free text — not necessarily in this hospital's Doctor table.",
    )

    preferred_language = models.CharField(max_length=8, choices=PreferredLanguage.choices, default=PreferredLanguage.MARATHI)

    is_active = models.BooleanField(default=True)

    # Household linking — e.g. a parent as the primary contact for a minor,
    # or a family sharing one phone number under one main contact.
    guardian = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="dependents",
        help_text="Primary contact/household head for this patient, if any.",
    )
    relationship_to_guardian = models.CharField(max_length=50, blank=True, help_text="e.g. child, spouse, parent")

    # Preventive-care / follow-up recall (§ retention) — swept by
    # apps.automation.tasks.sweep_patient_recalls, which creates a Task and
    # fires the patient_recall_due workflow trigger once this falls due.
    next_recall_due_at = models.DateTimeField(null=True, blank=True)
    recall_reason = models.CharField(max_length=255, blank=True, help_text="e.g. annual checkup, post-surgery follow-up, chronic-care review")

    class RegistrationType(models.TextChoices):
        OPD = "opd", "OPD"
        IPD = "ipd", "IPD"
        EMERGENCY = "emergency", "Emergency"

    class BloodGroup(models.TextChoices):
        A_POSITIVE = "a_positive", "A+"
        A_NEGATIVE = "a_negative", "A-"
        B_POSITIVE = "b_positive", "B+"
        B_NEGATIVE = "b_negative", "B-"
        AB_POSITIVE = "ab_positive", "AB+"
        AB_NEGATIVE = "ab_negative", "AB-"
        O_POSITIVE = "o_positive", "O+"
        O_NEGATIVE = "o_negative", "O-"

    # ERP identity — see docs/erp/02-domain-model.md. uhid is assigned once,
    # automatically, on first save (see save() below); it is the one
    # identifier this Patient row keeps for life, shared by every CRM and
    # ERP app that references this patient (docs/erp/00-overview.md §3 —
    # one Patient row, not a CRM/ERP duplicate).
    uhid = models.CharField(max_length=32, unique=True, null=True, blank=True, editable=False)
    mrn = models.CharField(max_length=64, blank=True, help_text="Legacy/external MRN, for hospitals migrating from another system.")
    registration_type = models.CharField(max_length=16, choices=RegistrationType.choices, blank=True)
    blood_group = models.CharField(max_length=16, choices=BloodGroup.choices, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["hospital", "mobile"]),
            models.Index(fields=["hospital", "next_recall_due_at"]),
        ]
        permissions = [
            # Capability flag, not tied to a CRUD verb — gates whether a
            # role's serializer includes clinical fields (diagnosis,
            # prescriptions, national ID) at all, vs. the CRM-safe subset.
            # See docs/erp/03-rbac-and-roles.md §2c. Codename deliberately
            # does NOT start with "view_"/"add_"/"change_"/"delete_" — a
            # permission_templates.py app-level verb sweep (e.g. any role
            # granted "patients": ["view", ...]) matches by codename
            # prefix, and "view_clinical_detail" would have been silently
            # swept into every such grant, including front_desk's.
            ("access_clinical_detail", "Can access clinical detail on patient records"),
        ]

    def save(self, *args, **kwargs):
        if not self.uhid and self.hospital_id:
            self.uhid = self._generate_uhid()
        super().save(*args, **kwargs)

    def _generate_uhid(self):
        from django.db import transaction

        from apps.core.models import Hospital

        with transaction.atomic():
            hospital = Hospital.objects.select_for_update().get(pk=self.hospital_id)
            sequence = hospital.next_uhid_sequence
            hospital.next_uhid_sequence = sequence + 1
            hospital.save(update_fields=["next_uhid_sequence"])
        return f"{hospital.slug.upper()}-{sequence:06d}"

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def full_name(self):
        return str(self)


class Document(TenantScopedModel):
    class Category(models.TextChoices):
        REPORT = "report", "Diagnostic report"
        PRESCRIPTION = "prescription", "Prescription"
        INSURANCE_CARD = "insurance_card", "Insurance card"
        CONSENT = "consent", "Consent form"
        ID_PROOF = "id_proof", "ID proof"
        OTHER = "other", "Other"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="documents")
    category = models.CharField(max_length=32, choices=Category.choices, default=Category.OTHER)
    title = models.CharField(max_length=255, blank=True)
    file = models.FileField(
        upload_to="patient_documents/%Y/%m/",
        validators=[
            FileExtensionValidator(allowed_extensions=ALLOWED_DOCUMENT_EXTENSIONS),
            validate_document_size,
        ],
    )
    notes = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    def __str__(self):
        return self.title or self.file.name


class TimelineEvent(TenantScopedModel):
    """Denormalized read model for the patient's full interaction timeline
    (§3). Other apps write into this via signals when a Call, Enquiry,
    Appointment, Message or Feedback happens, so the patient record can
    render one chronological feed with a single query instead of a fan-out
    across apps."""

    class EventType(models.TextChoices):
        CALL = "call", "Call"
        ENQUIRY = "enquiry", "Enquiry"
        APPOINTMENT = "appointment", "Appointment"
        MESSAGE = "message", "Message"
        FEEDBACK = "feedback", "Feedback"
        DOCUMENT = "document", "Document"
        NOTE = "note", "Note"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="timeline_events")
    event_type = models.CharField(max_length=16, choices=EventType.choices)
    summary = models.CharField(max_length=500)
    occurred_at = models.DateTimeField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    source = GenericForeignKey("content_type", "object_id")

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["patient", "-occurred_at"]),
        ]

    def __str__(self):
        return f"{self.get_event_type_display()}: {self.summary}"


def record_timeline_event(*, patient, event_type, summary, occurred_at, source=None, created_by=None):
    """Convenience helper for other apps' signal handlers."""
    return TimelineEvent.objects.create(
        hospital_id=patient.hospital_id,
        patient=patient,
        event_type=event_type,
        summary=summary,
        occurred_at=occurred_at,
        created_by=created_by,
        content_type=ContentType.objects.get_for_model(source) if source is not None else None,
        object_id=source.pk if source is not None else None,
    )


class Prescription(TenantScopedModel):
    """Lightweight OPD Doctor E-Prescription (e-Rx) model."""

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="prescriptions")
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    # String FK, not a direct import — apps.opd imports apps.patients
    # (Patient), so apps.patients importing apps.opd.Encounter back would
    # be circular. Nullable: pre-Phase-3 prescriptions and any created
    # outside a formal OPD encounter (phone-in refill, etc.) have none.
    encounter = models.ForeignKey("opd.Encounter", on_delete=models.SET_NULL, null=True, blank=True, related_name="prescriptions")

    diagnosis = models.CharField(max_length=500)
    symptoms = models.TextField(blank=True)
    medications = models.JSONField(default=list, blank=True)  # [{"name": "Paracetamol 500mg", "dosage": "1-0-1", "duration": "5 Days"}]
    lab_orders = models.JSONField(default=list, blank=True)   # ["CBC", "Chest X-Ray", "Lipid Profile"]
    notes = models.TextField(blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"e-Rx for {self.patient.full_name} - {self.diagnosis}"

