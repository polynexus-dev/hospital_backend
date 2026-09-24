"""
Patient portal (NABH AAC.2.f digital booking, AAC.3.l/AAC.4.k report
download, COP.1.l prescriptions, AAC.7.a care information, AAC.8.a/c/d
feedback, complaints, PROMs & PREMs, FPM.3.f account statement).

Patients sign in with their registered mobile number + OTP; one sign-in
covers every patient record (family members) registered on that number
at that hospital. Portal sessions are signed tokens, entirely separate
from staff JWTs — a portal token can never reach a staff endpoint.
"""
from django.db import models

from apps.core.models import TenantScopedModel


class CareInformation(TenantScopedModel):
    """AAC.7.a — what the hospital wants patients to know."""

    class Category(models.TextChoices):
        GENERAL = "general", "General (visiting hours, facilities)"
        RIGHTS = "rights", "Patient rights & responsibilities"
        EDUCATION = "education", "Health education"
        PREPARATION = "preparation", "Test / procedure preparation"
        DISCHARGE = "discharge", "Discharge & home care"
        TARIFF = "tariff", "Tariff / charges"

    category = models.CharField(max_length=12, choices=Category.choices, default=Category.GENERAL)
    title = models.CharField(max_length=200)
    body = models.TextField()
    language = models.CharField(max_length=4, default="en")
    is_published = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ["category", "order", "title"]


class PatientReportedMeasure(TenantScopedModel):
    """AAC.8.c PROMs / AAC.8.d PREMs. `answers` keyed by question id; the
    score is the mean of numeric answers (1–5 / 0–10 scales)."""

    class Kind(models.TextChoices):
        PROM = "prom", "Outcome (PROM)"
        PREM = "prem", "Experience (PREM)"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="reported_measures")
    kind = models.CharField(max_length=4, choices=Kind.choices)
    instrument = models.CharField(max_length=80, help_text="e.g. EQ-5D-5L, PROMIS-10, Inpatient experience")
    admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    answers = models.JSONField(default=dict)
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True, editable=False)
    comments = models.TextField(blank=True)
    submitted_via = models.CharField(max_length=10, default="portal")

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        nums = []
        for v in self.answers.values():
            try:
                nums.append(float(v))
            except (TypeError, ValueError):
                continue
        self.score = round(sum(nums) / len(nums), 2) if nums else None
        super().save(*args, **kwargs)


PROM_PREM_INSTRUMENTS = {
    "prem_inpatient": {
        "kind": "prem", "title": "Inpatient experience",
        "questions": [
            {"id": "q1", "text": "Doctors explained things in a way you could understand", "scale": 5},
            {"id": "q2", "text": "Nurses responded promptly when you needed help", "scale": 5},
            {"id": "q3", "text": "Your pain was well controlled", "scale": 5},
            {"id": "q4", "text": "Room and bathroom were kept clean", "scale": 5},
            {"id": "q5", "text": "You were told about medicines and side effects before discharge", "scale": 5},
            {"id": "q6", "text": "How likely are you to recommend this hospital (0–10)?", "scale": 10},
        ],
    },
    "prem_opd": {
        "kind": "prem", "title": "OPD experience",
        "questions": [
            {"id": "q1", "text": "Ease of booking the appointment", "scale": 5},
            {"id": "q2", "text": "Waiting time before consultation was acceptable", "scale": 5},
            {"id": "q3", "text": "The doctor listened to you", "scale": 5},
            {"id": "q4", "text": "Billing was clear", "scale": 5},
        ],
    },
    "prom_eq5d": {
        "kind": "prom", "title": "Health status (EQ-5D style)",
        "questions": [
            {"id": "mobility", "text": "Mobility (1 = no problems … 5 = unable)", "scale": 5},
            {"id": "self_care", "text": "Self-care (1 = no problems … 5 = unable)", "scale": 5},
            {"id": "usual_activities", "text": "Usual activities (1 = no problems … 5 = unable)", "scale": 5},
            {"id": "pain", "text": "Pain / discomfort (1 = none … 5 = extreme)", "scale": 5},
            {"id": "anxiety", "text": "Anxiety / depression (1 = none … 5 = extreme)", "scale": 5},
        ],
    },
}
