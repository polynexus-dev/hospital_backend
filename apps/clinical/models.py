"""
Cross-cutting clinical documentation & decision support (NABH HIS/EMR
chapter COP, plus AAC.1.h episodes of care): allergies, structured
assessments, risk scores, order sets, consent, handover, care plans,
clinical alerts / CDSS, notifiable-disease reporting, homecare, rehab
functional assessment, digital signatures and result review comments.

Everything here hangs off the patient's UHID-bearing Patient row (AAC.1.i)
and, where it applies, the specific OPD encounter or IPD admission.
"""
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone

from apps.core.fields import EncryptedTextField
from apps.core.models import TenantScopedModel

USER = settings.AUTH_USER_MODEL


class EpisodeOfCare(TenantScopedModel):
    """AAC.1.h — groups visits for one condition (e.g. a pregnancy, a
    chemotherapy course) under a single episode ID linked to the UHID."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="episodes")
    episode_code = models.CharField(max_length=40, editable=False)
    condition = models.CharField(max_length=255, help_text="The cause/condition this episode is about, e.g. 'Pregnancy 2026'.")
    icd_code = models.CharField(max_length=16, blank=True)
    specialty = models.CharField(max_length=80, blank=True)
    managing_doctor = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    started_on = models.DateField(default=timezone.localdate)
    ended_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-started_on", "-id"]
        constraints = [models.UniqueConstraint(fields=["hospital", "episode_code"], name="unique_episode_code_per_hospital")]

    def save(self, *args, **kwargs):
        if not self.episode_code:
            n = EpisodeOfCare.objects.filter(hospital_id=self.hospital_id, patient_id=self.patient_id).count() + 1
            self.episode_code = f"EP-{self.patient.uhid or self.patient_id}-{n:02d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.episode_code} — {self.condition}"


class Allergy(TenantScopedModel):
    """MOM.2.g — drives the allergy alert on prescribing and dispensing."""

    class AllergenType(models.TextChoices):
        DRUG = "drug", "Drug"
        FOOD = "food", "Food"
        ENVIRONMENTAL = "environmental", "Environmental"
        OTHER = "other", "Other"

    class Severity(models.TextChoices):
        MILD = "mild", "Mild"
        MODERATE = "moderate", "Moderate"
        SEVERE = "severe", "Severe / anaphylaxis"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        REFUTED = "refuted", "Refuted"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="allergies")
    allergen = models.CharField(max_length=150, help_text="For drugs, the generic name or class (e.g. 'penicillin').")
    allergen_type = models.CharField(max_length=16, choices=AllergenType.choices, default=AllergenType.DRUG)
    reaction = models.CharField(max_length=255, blank=True)
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.MODERATE)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    is_adverse_drug_reaction = models.BooleanField(default=False, help_text="An ADR rather than a true allergy.")
    noted_on = models.DateField(default=timezone.localdate)
    recorded_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-noted_on"]
        verbose_name_plural = "allergies"

    def __str__(self):
        return f"{self.allergen} ({self.get_severity_display()})"


class AssessmentTemplate(TenantScopedModel):
    """Configurable assessment forms (COP.1.a category-specific initial
    assessment / reassessment, COP.7.a dietary screening, COP.11.a rehab).
    `fields` is a list of {key, label, type, options?, required?} where
    type ∈ text|textarea|number|select|multiselect|boolean|date."""

    class Category(models.TextChoices):
        GENERAL = "general", "General medicine"
        ANTENATAL = "antenatal", "Antenatal"
        OBSTETRICS = "obstetrics", "Obstetrics"
        PAEDIATRICS = "paediatrics", "Paediatrics"
        OPHTHALMOLOGY = "ophthalmology", "Ophthalmology"
        ENT = "ent", "ENT"
        ONCOLOGY = "oncology", "Oncology"
        SURGERY = "surgery", "Surgery"
        NURSING = "nursing", "Nursing"
        DIETARY = "dietary", "Dietary"
        REHAB = "rehab", "Rehabilitation"
        PSYCHIATRY = "psychiatry", "Psychiatry"
        ORTHOPAEDICS = "orthopaedics", "Orthopaedics"
        CARDIOLOGY = "cardiology", "Cardiology"

    class Setting(models.TextChoices):
        OPD = "opd", "OPD"
        IPD = "ipd", "IPD"
        BOTH = "both", "OPD & IPD"

    name = models.CharField(max_length=150)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.GENERAL)
    setting = models.CharField(max_length=4, choices=Setting.choices, default=Setting.BOTH)
    fields = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "name"]

    def __str__(self):
        return self.name


class ClinicalAssessment(TenantScopedModel):
    class Kind(models.TextChoices):
        INITIAL = "initial", "Initial assessment"
        REASSESSMENT = "reassessment", "Re-assessment"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="assessments")
    encounter = models.ForeignKey("opd.Encounter", on_delete=models.SET_NULL, null=True, blank=True, related_name="assessments")
    admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="assessments")
    episode = models.ForeignKey(EpisodeOfCare, on_delete=models.SET_NULL, null=True, blank=True, related_name="assessments")
    template = models.ForeignKey(AssessmentTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    category = models.CharField(max_length=20, choices=AssessmentTemplate.Category.choices, default=AssessmentTemplate.Category.GENERAL)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.INITIAL)
    chief_complaint = models.CharField(max_length=500, blank=True)
    history = EncryptedTextField(blank=True)
    examination = EncryptedTextField(blank=True)
    provisional_diagnosis = models.CharField(max_length=500, blank=True)
    data = models.JSONField(default=dict, blank=True, help_text="Answers to the template's fields.")
    assessed_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    assessed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-assessed_at"]


class RiskAssessment(TenantScopedModel):
    """COP.9.a — validated tools; score and level are computed server-side
    (apps.clinical.scoring) so a client can't submit a wrong total."""

    class Tool(models.TextChoices):
        MORSE_FALL = "morse_fall", "Morse Fall Scale"
        BRADEN = "braden", "Braden (pressure ulcer)"
        CAPRINI = "caprini", "Caprini (VTE/DVT)"
        NEWS2 = "news2", "NEWS2 (early warning)"
        VULNERABILITY = "vulnerability", "Vulnerable patient screen"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="risk_assessments")
    admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="risk_assessments")
    tool = models.CharField(max_length=16, choices=Tool.choices)
    answers = models.JSONField(default=dict)
    score = models.IntegerField(default=0, editable=False)
    risk_level = models.CharField(max_length=20, blank=True, editable=False)
    interventions = models.TextField(blank=True)
    assessed_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    assessed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-assessed_at"]


class OrderSet(TenantScopedModel):
    """COP.1.c (medication order sets) / COP.1.i (lab & radiology order
    sets by diagnosis). `items`: [{type: medication|lab|radiology,
    ref_id?, name, dose?, frequency?, route?, duration?}]."""

    class Kind(models.TextChoices):
        MEDICATION = "medication", "Medication"
        LAB = "lab", "Laboratory"
        RADIOLOGY = "radiology", "Radiology"
        MIXED = "mixed", "Mixed"

    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.MIXED)
    diagnosis_codes = models.JSONField(default=list, blank=True, help_text="ICD-10 codes/prefixes this set applies to, e.g. ['N18', 'E11'].")
    diagnosis_keywords = models.JSONField(default=list, blank=True, help_text="Free-text keywords, e.g. ['kidney', 'ckd'].")
    items = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ConsentRecord(TenantScopedModel):
    """COP.6.c. Validation (clinical.serializers) insists on a guardian for
    minors and patients recorded as lacking capacity."""

    class ConsentType(models.TextChoices):
        GENERAL = "general", "General treatment"
        PROCEDURE = "procedure", "Procedure / surgery"
        ANAESTHESIA = "anaesthesia", "Anaesthesia / sedation"
        BLOOD = "blood", "Blood transfusion"
        HIGH_RISK = "high_risk", "High-risk procedure"
        RESEARCH = "research", "Research"
        INFORMATION_SHARING = "information_sharing", "Information sharing"
        TELEMEDICINE = "telemedicine", "Teleconsultation"
        HIV_TEST = "hiv_test", "HIV testing"
        CHEMOTHERAPY = "chemotherapy", "Chemotherapy"
        RADIOTHERAPY = "radiotherapy", "Radiotherapy"
        PHOTOGRAPHY = "photography", "Photography / recording"

    class GivenBy(models.TextChoices):
        PATIENT = "patient", "Patient"
        GUARDIAN = "guardian", "Legal guardian / next of kin"

    class Status(models.TextChoices):
        GRANTED = "granted", "Granted"
        REFUSED = "refused", "Refused"
        WITHDRAWN = "withdrawn", "Withdrawn"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="consents")
    admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="consents")
    surgery_request = models.ForeignKey("ot.SurgeryRequest", on_delete=models.SET_NULL, null=True, blank=True, related_name="consents")
    consent_type = models.CharField(max_length=24, choices=ConsentType.choices)
    procedure_name = models.CharField(max_length=255, blank=True)
    risks_explained = models.TextField(blank=True)
    alternatives_explained = models.TextField(blank=True)
    language = models.CharField(max_length=20, default="en", help_text="Language the consent was explained in.")
    patient_lacks_capacity = models.BooleanField(default=False, help_text="Unconscious, cognitively impaired or otherwise unable to consent.")
    given_by = models.CharField(max_length=10, choices=GivenBy.choices, default=GivenBy.PATIENT)
    guardian_name = models.CharField(max_length=150, blank=True)
    guardian_relation = models.CharField(max_length=60, blank=True)
    witness_name = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.GRANTED)
    signature_image = models.ImageField(upload_to="consents/", blank=True)
    signed_document = models.FileField(upload_to="consents/", blank=True)
    obtained_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    obtained_at = models.DateTimeField(default=timezone.now)
    valid_until = models.DateField(null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    withdrawal_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-obtained_at"]


class ShiftHandover(TenantScopedModel):
    """COP.2.b — SBAR handover, acknowledged by the receiving clinician."""

    class Role(models.TextChoices):
        NURSE = "nurse", "Nursing"
        DOCTOR = "doctor", "Medical"

    class Shift(models.TextChoices):
        MORNING = "morning", "Morning"
        EVENING = "evening", "Evening"
        NIGHT = "night", "Night"

    admission = models.ForeignKey("ipd.Admission", on_delete=models.CASCADE, related_name="handovers")
    handover_role = models.CharField(max_length=8, choices=Role.choices, default=Role.NURSE)
    shift = models.CharField(max_length=8, choices=Shift.choices)
    handed_over_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="+")
    handed_over_to = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    situation = models.TextField()
    background = models.TextField(blank=True)
    assessment = models.TextField(blank=True, help_text="Current condition, vitals, recent changes.")
    recommendation = models.TextField(blank=True, help_text="Pending tasks, scheduled investigations/procedures, watch-outs.")
    vitals_snapshot = models.JSONField(default=dict, blank=True)
    handed_over_at = models.DateTimeField(default=timezone.now)
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-handed_over_at"]


class CarePlan(TenantScopedModel):
    """COP.13.a. goals/interventions: [{text, target_date?, status}]."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ON_HOLD = "on_hold", "On hold"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="care_plans")
    admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="care_plans")
    title = models.CharField(max_length=200)
    problem = models.TextField()
    goals = models.JSONField(default=list)
    interventions = models.JSONField(default=list)
    start_date = models.DateField(default=timezone.localdate)
    review_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    created_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-start_date"]


class DrugInteraction(TenantScopedModel):
    """CDSS knowledge base (COP.12.a/b). Matching is on lower-cased
    generic-name substrings, so 'aspirin' matches 'Aspirin 75mg'."""

    class Severity(models.TextChoices):
        MINOR = "minor", "Minor"
        MODERATE = "moderate", "Moderate"
        MAJOR = "major", "Major"
        CONTRAINDICATED = "contraindicated", "Contraindicated"

    drug_a = models.CharField(max_length=120)
    drug_b = models.CharField(max_length=120)
    severity = models.CharField(max_length=16, choices=Severity.choices, default=Severity.MODERATE)
    description = models.TextField()
    recommendation = models.TextField(blank=True)

    class Meta:
        ordering = ["drug_a", "drug_b"]


class DrugConditionRule(TenantScopedModel):
    """Drug–condition contraindication / dosing rules, e.g. metformin with
    eGFR<30, NSAIDs in CKD, contrast in pregnancy (also used for AAC.4.l
    radiology contraindications — drug is then the modality/contrast)."""

    drug = models.CharField(max_length=120, help_text="Generic name, class, or radiology modality/contrast.")
    condition_keywords = models.JSONField(default=list, help_text="Diagnosis text / ICD prefixes, or patient flags like 'pregnant', 'age>65'.")
    severity = models.CharField(max_length=16, choices=DrugInteraction.Severity.choices, default=DrugInteraction.Severity.MAJOR)
    message = models.TextField()
    applies_to = models.CharField(max_length=12, default="medication", help_text="medication | radiology")

    class Meta:
        ordering = ["drug"]


class ClinicalAlert(TenantScopedModel):
    """Every alert the system raises for a patient lands here — the
    clinician's alert inbox and the audit of what was shown and whether it
    was acknowledged or overridden."""

    class AlertType(models.TextChoices):
        CRITICAL_RESULT = "critical_result", "Critical result"
        ALLERGY = "allergy", "Allergy"
        INTERACTION = "interaction", "Drug interaction"
        DUPLICATE_ORDER = "duplicate_order", "Duplicate order"
        CONTRAINDICATION = "contraindication", "Contraindication"
        HIGH_RISK_MEDICATION = "high_risk_medication", "High-risk medication"
        NOTIFIABLE_DISEASE = "notifiable_disease", "Notifiable disease"
        EARLY_WARNING = "early_warning", "Early warning score"
        RISK = "risk", "Risk assessment"
        CDSS = "cdss", "Clinical decision support"
        EXPIRY = "expiry", "Expiry / stock"
        NEW_ORDER = "new_order", "New order for department"

    class Severity(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, null=True, blank=True, related_name="clinical_alerts")
    alert_type = models.CharField(max_length=24, choices=AlertType.choices)
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.WARNING)
    title = models.CharField(max_length=200)
    message = models.TextField()
    target_user = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="clinical_alerts")
    target_department = models.CharField(max_length=60, blank=True)
    content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    source = GenericForeignKey("content_type", "object_id")
    acknowledged_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    override_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["hospital", "acknowledged_at", "severity"])]


class NotifiableDisease(TenantScopedModel):
    """COP.12.c — the state/UT's notifiable disease list."""

    name = models.CharField(max_length=150)
    icd_codes = models.JSONField(default=list, help_text="ICD-10 codes/prefixes, e.g. ['A90', 'A91'] for dengue.")
    keywords = models.JSONField(default=list)
    authority = models.CharField(max_length=150, default="District Surveillance Officer (IDSP / IHIP)")
    report_within_hours = models.PositiveSmallIntegerField(default=24)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class NotifiableDiseaseReport(TenantScopedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending report"
        REPORTED = "reported", "Reported"
        NOT_REQUIRED = "not_required", "Not required (ruled out)"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="notifiable_reports")
    disease = models.ForeignKey(NotifiableDisease, on_delete=models.PROTECT, related_name="reports")
    diagnosis_text = models.CharField(max_length=255)
    icd_code = models.CharField(max_length=16, blank=True)
    detected_at = models.DateTimeField(default=timezone.now)
    due_by = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.PENDING)
    reported_at = models.DateTimeField(null=True, blank=True)
    reported_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reference_number = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["-detected_at"]


class ResultReview(TenantScopedModel):
    """COP.1.j — a clinician's review comment on an imported result /
    record from another department."""

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="result_reviews")
    source_type = models.CharField(max_length=30, help_text="lab_result | radiology_report | document | external | ...")
    source_id = models.CharField(max_length=64, blank=True)
    summary = models.CharField(max_length=300, blank=True)
    comment = models.TextField()
    reviewed_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]


class DigitalSignature(TenantScopedModel):
    """COP.1.e — a re-authenticated sign-off on any clinical document.
    `document_hash` is a SHA-256 over the document's content at signing
    time, so a later change to the record is detectable."""

    class Method(models.TextChoices):
        PASSWORD = "password", "Password re-authentication"
        TOTP = "totp", "Authenticator OTP"
        STYLUS = "stylus", "Drawn signature"

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField(max_length=64)
    document = GenericForeignKey("content_type", "object_id")
    signer = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="+")
    signer_name = models.CharField(max_length=200)
    signer_registration_number = models.CharField(max_length=64, blank=True)
    method = models.CharField(max_length=10, choices=Method.choices)
    signature_image = models.ImageField(upload_to="signatures/signed/", blank=True)
    document_hash = models.CharField(max_length=64)
    signed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-signed_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]


class HomecareService(TenantScopedModel):
    class Kind(models.TextChoices):
        SAMPLE_COLLECTION = "sample_collection", "Home sample collection"
        NURSING = "nursing", "Nursing care"
        PHYSIOTHERAPY = "physiotherapy", "Physiotherapy"
        DOCTOR_VISIT = "doctor_visit", "Doctor visit"
        WEARABLE = "wearable", "Wearable / remote monitoring"
        MEDICINE_DELIVERY = "medicine_delivery", "Medicine delivery"

    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class HomecareBooking(TenantScopedModel):
    """COP.10.b — booking → assignment → visit (with vitals) → billing →
    feedback."""

    class Status(models.TextChoices):
        BOOKED = "booked", "Booked"
        ASSIGNED = "assigned", "Staff assigned"
        EN_ROUTE = "en_route", "En route"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="homecare_bookings")
    service = models.ForeignKey(HomecareService, on_delete=models.PROTECT, related_name="bookings")
    scheduled_at = models.DateTimeField()
    address = EncryptedTextField()
    assigned_staff = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.BOOKED)
    visit_notes = models.TextField(blank=True)
    vitals = models.JSONField(default=dict, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    bill = models.ForeignKey("billing.Bill", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    feedback_score = models.PositiveSmallIntegerField(null=True, blank=True)
    feedback_comment = models.TextField(blank=True)

    class Meta:
        ordering = ["-scheduled_at"]


class FunctionalAssessment(TenantScopedModel):
    """COP.11.a — rehab scales. `scores` holds item scores; `total` is
    computed server-side for the scales we know (Barthel)."""

    class Discipline(models.TextChoices):
        PHYSIOTHERAPY = "physiotherapy", "Physiotherapy"
        OCCUPATIONAL = "occupational", "Occupational therapy"
        SPEECH = "speech", "Speech therapy"
        CARDIAC_REHAB = "cardiac_rehab", "Cardiac rehab"

    class Scale(models.TextChoices):
        BARTHEL = "barthel", "Barthel Index (ADL)"
        FIM = "fim", "Functional Independence Measure"
        MRC = "mrc", "MRC muscle power"
        ROM = "rom", "Range of motion"
        PAIN_VAS = "pain_vas", "Pain VAS"
        CUSTOM = "custom", "Custom"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="functional_assessments")
    discipline = models.CharField(max_length=16, choices=Discipline.choices)
    scale = models.CharField(max_length=10, choices=Scale.choices)
    scores = models.JSONField(default=dict)
    total = models.IntegerField(null=True, blank=True)
    goals = models.TextField(blank=True)
    previous = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="reassessments")
    assessed_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    assessed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-assessed_at"]


class SpecialtyReferral(TenantScopedModel):
    """AAC.1.k — internal cross-specialty referral with a response loop."""

    class Urgency(models.TextChoices):
        ROUTINE = "routine", "Routine"
        URGENT = "urgent", "Urgent"
        EMERGENCY = "emergency", "Emergency"

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        ACCEPTED = "accepted", "Accepted"
        SEEN = "seen", "Seen / opinion given"
        DECLINED = "declined", "Declined"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="specialty_referrals")
    admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    from_doctor = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="+")
    to_department = models.ForeignKey("core.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    to_doctor = models.ForeignKey("appointments.Doctor", on_delete=models.SET_NULL, null=True, blank=True, related_name="incoming_referrals")
    reason = models.TextField()
    clinical_summary = models.TextField(blank=True)
    urgency = models.CharField(max_length=10, choices=Urgency.choices, default=Urgency.ROUTINE)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SENT)
    response = models.TextField(blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class RecordShare(TenantScopedModel):
    """AAC.1.j — share a patient's record with an affiliate facility of
    the same hospital group (blood bank, satellite lab, sister hospital)
    for a limited time; the receiving hospital can then open the
    clinical summary. Every access is audit-logged."""

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="record_shares")
    shared_with = models.ForeignKey("core.Hospital", on_delete=models.CASCADE, related_name="records_shared_with_me")
    purpose = models.CharField(max_length=200)
    patient_consent = models.BooleanField(default=False)
    shared_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="+")
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class MedicalDevice(TenantScopedModel):
    """AAC.1.l / COP.5.c — bedside monitors, ventilators, biometric
    readers, scanners and printers registered with the HIS. Monitors push
    vitals with their token; the bed's current admission receives them."""

    class Kind(models.TextChoices):
        MONITOR = "monitor", "Patient monitor"
        VENTILATOR = "ventilator", "Ventilator"
        INFUSION_PUMP = "infusion_pump", "Infusion pump"
        GLUCOMETER = "glucometer", "Glucometer"
        BIOMETRIC = "biometric", "Biometric reader"
        BARCODE_SCANNER = "barcode_scanner", "Barcode scanner"
        PRINTER = "printer", "Label / report printer"
        OTHER = "other", "Other"

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    serial_number = models.CharField(max_length=100, blank=True)
    bed = models.ForeignKey("facilities.Bed", on_delete=models.SET_NULL, null=True, blank=True, related_name="devices")
    location = models.CharField(max_length=100, blank=True)
    api_token = models.CharField(max_length=64, blank=True, editable=False)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name


class DeviceVitalSign(TenantScopedModel):
    device = models.ForeignKey(MedicalDevice, on_delete=models.SET_NULL, null=True, related_name="readings")
    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="device_vitals")
    admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="device_vitals")
    recorded_at = models.DateTimeField(default=timezone.now, db_index=True)
    heart_rate = models.PositiveSmallIntegerField(null=True, blank=True)
    spo2 = models.PositiveSmallIntegerField(null=True, blank=True)
    respiratory_rate = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_systolic = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic = models.PositiveSmallIntegerField(null=True, blank=True)
    temperature_c = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    etco2 = models.PositiveSmallIntegerField(null=True, blank=True)
    extra = models.JSONField(default=dict, blank=True)
    news2_score = models.PositiveSmallIntegerField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-recorded_at"]
