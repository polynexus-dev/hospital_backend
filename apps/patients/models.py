from datetime import date

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator, FileExtensionValidator
from django.db import models

from apps.core.encryption import blind_index, email_index, name_query_tokens, name_search_tokens, normalize_phone
from apps.core.fields import EncryptedCharField, EncryptedDateField, EncryptedJSONField, EncryptedTextField
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

    def search_name(self, term):
        """Patients whose name has a word starting with each word of `term`
        (case-insensitive), e.g. "ash rao" finds "Ashwini Rao-Iyer"."""
        tokens = name_query_tokens(term)
        if not tokens:
            return self.none()
        qs = self
        for token in tokens:
            qs = qs.filter(name_search__contains=f" {token} ")
        return qs

    def by_email(self, email):
        digest = email_index(email)
        return self.filter(email_hash=digest) if digest else self.none()


class PatientManager(SoftDeleteManager):
    queryset_class = PatientQuerySet

    def by_mobile(self, mobile):
        return self.get_queryset().by_mobile(mobile)

    def search_name(self, term):
        return self.get_queryset().search_name(term)

    def by_email(self, email):
        return self.get_queryset().by_email(email)


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

    # Name, date of birth and email are encrypted at rest (DPDP Act 2023):
    # searched through name_search / email_hash, aged through birth_year.
    title = models.CharField(max_length=12, blank=True, help_text="Mr, Mrs, Ms, Master, Baby, Dr …")
    first_name = EncryptedCharField(max_length=150)
    last_name = EncryptedCharField(max_length=150, blank=True)
    date_of_birth = EncryptedDateField(null=True, blank=True)
    name_search = models.TextField(blank=True, editable=False)
    birth_year = models.PositiveSmallIntegerField(null=True, blank=True, editable=False, db_index=True)
    # Registered by age only ("about 45"): the date of birth is 1 July of the derived year.
    dob_is_estimated = models.BooleanField(default=False)
    gender = models.CharField(max_length=16, choices=Gender.choices, blank=True)

    # Encrypted at rest (Part A #2)
    mobile = EncryptedCharField(max_length=20)
    alternate_mobile = EncryptedCharField(max_length=20, blank=True)
    mobile_hash = models.CharField(max_length=64, blank=True, editable=False, db_index=True)
    alternate_mobile_hash = models.CharField(max_length=64, blank=True, editable=False, db_index=True)
    email = EncryptedCharField(max_length=254, blank=True, validators=[EmailValidator()])
    whatsapp = EncryptedCharField(max_length=20, blank=True, help_text="Blank = same as the mobile number.")
    email_hash = models.CharField(max_length=64, blank=True, editable=False, db_index=True)

    address = EncryptedTextField(blank=True)
    city = models.CharField(max_length=120, blank=True)
    # Structured address (MoHFW EHR / MDDS demographic set). Kept plain so
    # reports can group by area; the street address above stays encrypted.
    country = models.CharField(max_length=2, default="IN", help_text="ISO 3166-1 alpha-2")
    state = models.CharField(max_length=80, blank=True, db_index=True)
    district = models.CharField(max_length=80, blank=True)
    sub_district = models.CharField(max_length=80, blank=True, help_text="Taluka / tehsil / mandal")
    village_or_area = models.CharField(max_length=120, blank=True)
    pincode = models.CharField(max_length=6, blank=True, db_index=True)
    nationality = models.CharField(max_length=2, default="IN", help_text="ISO 3166-1 alpha-2")
    passport_number = EncryptedCharField(max_length=20, blank=True, help_text="Foreign nationals only.")
    is_vvip = models.BooleanField(default=False, help_text="Shown only to roles that may see VIP flags.")
    tags = models.JSONField(default=list, blank=True, help_text='e.g. ["senior citizen", "staff family"]')

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

    # NABH AAC.1.a/b/g
    mobile_verified_at = models.DateTimeField(null=True, blank=True, editable=False)
    registration_channel = models.CharField(max_length=16, default="front_desk", help_text="front_desk | kiosk | website | app | qr | abdm_scan_share | offline")
    payment_preference = models.CharField(max_length=20, blank=True, help_text="cash | card | upi | insurance | corporate")
    offline_client_id = models.CharField(max_length=64, blank=True, db_index=True, help_text="Client-generated id for idempotent offline sync.")

    objects = PatientManager()

    # Encrypted fields and the columns that make them searchable (apps.core.filters).
    SEARCH_INDEXES = {
        "name_search": ("name", ["first_name", "last_name"]),
        "mobile_hash": ("phone", ["mobile"]),
        "alternate_mobile_hash": ("phone", ["alternate_mobile"]),
        "email_hash": ("email", ["email"]),
    }

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
        self.name_search = name_search_tokens(self.first_name, self.last_name)
        self.email_hash = email_index(self.email)
        dob = self.date_of_birth
        if isinstance(dob, str):
            dob = date.fromisoformat(dob) if dob else None
        self.birth_year = dob.year if dob else None
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"name_search", "email_hash", "birth_year", "mobile_hash", "alternate_mobile_hash"}
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
        config = UHIDConfig.objects.filter(hospital_id=self.hospital_id).first()
        if config is not None:
            return config.format(sequence)
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
    requirement = models.ForeignKey("DocumentRequirement", on_delete=models.SET_NULL, null=True, blank=True, related_name="documents",
                                    help_text="The checklist item this upload meets, if any.")
    uploaded_via_portal = models.BooleanField(default=False)

    objects = SoftDeleteManager()

    def __str__(self):
        return self.title or self.file.name


class DocumentRequirement(TenantScopedModel):
    """A document the hospital needs at a stage of care (registration,
    admission, a radiology study...). Patients' uploads are checked against
    these (apps.patients.checklist)."""

    class Stage(models.TextChoices):
        REGISTRATION = "registration", "Registration"
        ADMISSION = "admission", "Admission"
        SURGERY = "surgery", "Surgery"
        RADIOLOGY = "radiology", "Radiology"
        LABORATORY = "laboratory", "Laboratory"
        INSURANCE = "insurance", "Insurance / TPA claim"

    stage = models.CharField(max_length=16, choices=Stage.choices)
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=32, choices=Document.Category.choices, default=Document.Category.OTHER)
    required = models.BooleanField(default=True)
    only_if_insured = models.BooleanField(default=False, help_text="Needed only for patients with an insurer on record.")
    valid_days = models.PositiveIntegerField(null=True, blank=True, help_text="Upload must be this recent (e.g. a consent); empty = any time.")
    reuse_same_category = models.BooleanField(default=True, help_text="Any upload of this category counts (an ID proof uploaded once counts everywhere).")
    help_text = models.CharField(max_length=200, blank=True)
    order = models.PositiveSmallIntegerField(default=10)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["stage", "order", "name"]

    def __str__(self):
        return f"{self.get_stage_display()}: {self.name}"


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



class UHIDConfig(models.Model):
    """NABH AAC.1.d — hospital-defined UHID format. Tokens in `pattern`:
    {PREFIX} {BRANCH} {YYYY} {YY} {MM} {SEQ} {SUFFIX}. The sequence itself
    stays the hospital-wide counter (Hospital.next_uhid_sequence), so a
    format change never creates collisions with numbers already issued."""

    hospital = models.OneToOneField("core.Hospital", on_delete=models.CASCADE, related_name="uhid_config")
    prefix = models.CharField(max_length=10, blank=True)
    branch_code = models.CharField(max_length=10, blank=True)
    suffix = models.CharField(max_length=10, blank=True)
    pattern = models.CharField(max_length=80, default="{PREFIX}{BRANCH}{YY}{SEQ}")
    sequence_digits = models.PositiveSmallIntegerField(default=6)

    def format(self, sequence, when=None):
        from django.utils import timezone

        when = when or timezone.localdate()
        out = self.pattern
        for token, value in {
            "{PREFIX}": self.prefix, "{BRANCH}": self.branch_code, "{SUFFIX}": self.suffix,
            "{YYYY}": f"{when:%Y}", "{YY}": f"{when:%y}", "{MM}": f"{when:%m}", "{SEQ}": str(sequence).zfill(self.sequence_digits),
        }.items():
            out = out.replace(token, value)
        return out.upper()[:32]


class MobileOTP(models.Model):
    """AAC.1.b — mobile verification (also reused by the patient portal).
    Only a hash of the code is stored."""

    hospital = models.ForeignKey("core.Hospital", on_delete=models.CASCADE, related_name="+")
    mobile_hash = models.CharField(max_length=64, db_index=True)
    purpose = models.CharField(max_length=20, default="registration")
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class MobileUploadSession(TenantScopedModel):
    """A short-lived link (shown as a QR code) that lets a phone upload
    documents straight into a patient's record without signing in."""

    patient = models.ForeignKey("Patient", on_delete=models.CASCADE, related_name="+")
    category = models.CharField(max_length=32, default="other")
    requirement = models.ForeignKey("DocumentRequirement", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    expires_at = models.DateTimeField()
    max_files = models.PositiveSmallIntegerField(default=10)
    uploads = models.PositiveSmallIntegerField(default=0)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_open(self):
        from django.utils import timezone

        return self.closed_at is None and self.expires_at > timezone.now() and self.uploads < self.max_files
