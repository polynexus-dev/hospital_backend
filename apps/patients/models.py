from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.core.models import TenantScopedModel


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
    national_id_number = models.CharField(max_length=64, blank=True)

    insurance_provider = models.CharField(max_length=150, blank=True)
    insurance_policy_number = models.CharField(max_length=100, blank=True)
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

    class Meta:
        indexes = [
            models.Index(fields=["hospital", "mobile"]),
        ]

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
    file = models.FileField(upload_to="patient_documents/%Y/%m/")
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

