from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models

from apps.core.encryption import blind_index, normalize_phone
from apps.core.fields import EncryptedCharField, EncryptedJSONField, EncryptedTextField
from apps.core.managers import SoftDeleteManager, SoftDeleteQuerySet
from apps.core.models import SoftDeleteModel, TenantScopedModel

MAX_DOCUMENT_SIZE_MB = 10
ALLOWED_DOCUMENT_EXTENSIONS = ["pdf", "jpg", "jpeg", "png", "doc", "docx"]


def validate_document_size(value):
    filesize = value.size
    if filesize > MAX_DOCUMENT_SIZE_MB * 1024 * 1024:
        raise ValidationError(f"Maximum file size is {MAX_DOCUMENT_SIZE_MB}MB.")


class PatientQuerySet(SoftDeleteQuerySet):
    def by_mobile(self, mobile):
        """Exact-match lookup by phone number against the blind-index
        columns — mobile/alternate_mobile are encrypted (Part A #2) and
        therefore not directly filterable, see apps.core.fields."""
        digest = blind_index(normalize_phone(mobile))
        if not digest:
            return self.none()
        return self.filter(models.Q(mobile_hash=digest) | models.Q(alternate_mobile_hash=digest))


class PatientManager(SoftDeleteManager):
    queryset_class = PatientQuerySet


class Patient(TenantScopedModel, SoftDeleteModel):
    class Gender(models.TextChoices):
        MALE = "male", "Male"
        FEMALE = "female", "Female"
        OTHER = "other", "Other"
        UNDISCLOSED = "undisclosed", "Prefer not to say"

    class PreferredLanguage(models.TextChoices):
        MARATHI = "mr", "Marathi"
        HINDI = "hi", "Hindi"
        ENGLISH = "en", "English"

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

    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=16, choices=Gender.choices, blank=True)

    # Encrypted at rest (Part A #2)
    mobile = EncryptedCharField(max_length=20)
    alternate_mobile = EncryptedCharField(max_length=20, blank=True)
    mobile_hash = models.CharField(max_length=64, blank=True, editable=False, db_index=True)
    alternate_mobile_hash = models.CharField(max_length=64, blank=True, editable=False, db_index=True)
    email = models.EmailField(blank=True)

    address = EncryptedTextField(blank=True)
    city = models.CharField(max_length=120, blank=True)

    national_id_type = models.CharField(max_length=32, blank=True, help_text="e.g. Aadhaar, PAN, Passport")
    national_id_number = EncryptedCharField(max_length=64, blank=True)

    insurance_provider = models.CharField(max_length=150, blank=True)
    # Encrypted at rest (Part A #2) — same reasoning as national_id_number
    # above. Was briefly reverted to plaintext by a merge conflict
    # resolution; restored here, see apps.patients.migrations.0014.
    insurance_policy_number = EncryptedCharField(max_length=100, blank=True)
    employer = models.CharField(max_length=150, blank=True)

    attendant_name = models.CharField(max_length=150, blank=True)
    # Encrypted at rest (Part A #2) — a phone number identifying a
    # non-patient third party (attendant/guardian), same sensitivity class
    # as Patient.mobile.
    attendant_phone = EncryptedCharField(max_length=20, blank=True)
    attendant_relation = models.CharField(max_length=50, blank=True, help_text="e.g. daughter, spouse")
    referring_doctor_name = models.CharField(
        max_length=150,
        blank=True,
        help_text="External referring doctor, free text — not necessarily in this hospital's Doctor table.",
    )

    preferred_language = models.CharField(max_length=8, choices=PreferredLanguage.choices, default=PreferredLanguage.MARATHI)

    is_active = models.BooleanField(default=True)

    guardian = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="dependents",
        help_text="Primary contact/household head for this patient, if any.",
    )
    relationship_to_guardian = models.CharField(max_length=50, blank=True, help_text="e.g. child, spouse, parent")

    next_recall_due_at = models.DateTimeField(null=True, blank=True)
    recall_reason = models.CharField(max_length=255, blank=True, help_text="e.g. annual checkup, post-surgery follow-up, chronic-care review")

    uhid = models.CharField(max_length=32, unique=True, null=True, blank=True, editable=False)
    mrn = models.CharField(max_length=64, blank=True, help_text="Legacy/external MRN, for hospitals migrating from another system.")
    registration_type = models.CharField(max_length=16, choices=RegistrationType.choices, blank=True)
    blood_group = models.CharField(max_length=16, choices=BloodGroup.choices, blank=True)

    objects = PatientManager()

    class Meta:
        indexes = [
            models.Index(fields=["hospital", "mobile_hash"]),
            models.Index(fields=["hospital", "next_recall_due_at"]),
        ]
        permissions = [
            ("access_clinical_detail", "Can access clinical detail on patient records"),
        ]

    def save(self, *args, **kwargs):
        self.mobile_hash = blind_index(normalize_phone(self.mobile))
        self.alternate_mobile_hash = blind_index(normalize_phone(self.alternate_mobile))
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


class Document(TenantScopedModel, SoftDeleteModel):
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
    notes = EncryptedTextField(blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    objects = SoftDeleteManager()

    def __str__(self):
        return self.title or self.file.name


class TimelineEvent(TenantScopedModel):
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


class Prescription(TenantScopedModel, SoftDeleteModel):
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="prescriptions")
    doctor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    encounter = models.ForeignKey("opd.Encounter", on_delete=models.SET_NULL, null=True, blank=True, related_name="prescriptions")

    diagnosis = EncryptedCharField(max_length=500)
    symptoms = EncryptedTextField(blank=True)
    medications = EncryptedJSONField(default=list, blank=True)
    lab_orders = EncryptedJSONField(default=list, blank=True)
    notes = EncryptedTextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    objects = SoftDeleteManager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"e-Rx for {self.patient.full_name} - {self.diagnosis}"
