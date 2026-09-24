"""
Patient safety & quality (NABH HIS/EMR COP.8.c, COP.4.d, COP.3.d, MOM.4.b-d,
IMS.2.a-c): incident & sentinel-event reporting, medication errors,
emergency codes, mock drills, reusable checklists, and the NABH KPI engine
(apps.quality.kpis) with manual entry for audit-based indicators.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel

USER = settings.AUTH_USER_MODEL


class SafetyIncident(TenantScopedModel):
    class IncidentType(models.TextChoices):
        FALL = "fall", "Patient fall"
        MEDICATION_ERROR = "medication_error", "Medication error"
        ADVERSE_DRUG_REACTION = "adr", "Adverse drug reaction"
        TRANSFUSION_REACTION = "transfusion_reaction", "Transfusion reaction (haemovigilance)"
        PRESSURE_ULCER = "pressure_ulcer", "Hospital-acquired pressure ulcer"
        WRONG_SITE = "wrong_site", "Wrong site / patient / procedure"
        RETAINED_ITEM = "retained_item", "Retained foreign object"
        NEEDLESTICK = "needlestick", "Needlestick injury"
        EQUIPMENT = "equipment", "Equipment failure"
        IDENTIFICATION = "identification", "Patient identification error"
        DIAGNOSTIC = "diagnostic", "Diagnostic / reporting error"
        VIOLENCE = "violence", "Violence / security"
        DEATH_UNEXPECTED = "unexpected_death", "Unexpected death"
        OTHER = "other", "Other"

    class Harm(models.TextChoices):
        NEAR_MISS = "near_miss", "Near miss (did not reach patient)"
        NO_HARM = "no_harm", "Reached patient — no harm"
        MILD = "mild", "Mild harm"
        MODERATE = "moderate", "Moderate harm"
        SEVERE = "severe", "Severe harm"
        DEATH = "death", "Death"

    class Status(models.TextChoices):
        REPORTED = "reported", "Reported"
        INVESTIGATING = "investigating", "Under investigation"
        RCA_DONE = "rca_done", "RCA completed"
        CLOSED = "closed", "Closed"

    incident_type = models.CharField(max_length=24, choices=IncidentType.choices)
    harm = models.CharField(max_length=10, choices=Harm.choices, default=Harm.NO_HARM)
    is_sentinel = models.BooleanField(default=False)
    patient = models.ForeignKey("patients.Patient", on_delete=models.SET_NULL, null=True, blank=True, related_name="safety_incidents")
    admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="safety_incidents")
    staff_involved = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    location = models.CharField(max_length=100, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    description = models.TextField()
    immediate_action = models.TextField(blank=True)
    reported_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    is_anonymous = models.BooleanField(default=False)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.REPORTED)
    root_cause = models.TextField(blank=True)
    corrective_action = models.TextField(blank=True)
    preventive_action = models.TextField(blank=True)
    capa_owner = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    capa_due = models.DateField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    SENTINEL_TYPES = {"wrong_site", "retained_item", "unexpected_death"}

    class Meta:
        ordering = ["-occurred_at"]

    def save(self, *args, **kwargs):
        if self.incident_type in self.SENTINEL_TYPES or self.harm in (self.Harm.SEVERE, self.Harm.DEATH):
            self.is_sentinel = True
        super().save(*args, **kwargs)


class MedicationError(TenantScopedModel):
    """MOM.4.b — each error also opens a SafetyIncident for RCA."""

    class Stage(models.TextChoices):
        PRESCRIBING = "prescribing", "Prescribing"
        TRANSCRIBING = "transcribing", "Transcribing"
        DISPENSING = "dispensing", "Dispensing"
        ADMINISTRATION = "administration", "Administration"
        MONITORING = "monitoring", "Monitoring"

    class ErrorType(models.TextChoices):
        WRONG_DRUG = "wrong_drug", "Wrong drug"
        WRONG_DOSE = "wrong_dose", "Wrong dose / overdose"
        WRONG_ROUTE = "wrong_route", "Wrong route"
        WRONG_TIME = "wrong_time", "Wrong time / omission"
        WRONG_PATIENT = "wrong_patient", "Wrong patient"
        ALLERGY_IGNORED = "allergy", "Known allergy"
        INTERACTION = "interaction", "Drug interaction"
        LASA = "lasa", "Look-alike / sound-alike mix-up"
        ABBREVIATION = "abbreviation", "Error-prone abbreviation / illegible"
        EXPIRED = "expired", "Expired drug"
        OTHER = "other", "Other"

    class Category(models.TextChoices):
        # NCC MERP index
        A = "A", "A — circumstances with capacity to cause error"
        B = "B", "B — error occurred, did not reach patient"
        C = "C", "C — reached patient, no harm"
        D = "D", "D — reached patient, needed monitoring"
        E = "E", "E — temporary harm, intervention needed"
        F = "F", "F — temporary harm, prolonged hospitalisation"
        G = "G", "G — permanent harm"
        H = "H", "H — intervention to sustain life"
        I = "I", "I — death"

    patient = models.ForeignKey("patients.Patient", on_delete=models.SET_NULL, null=True, blank=True, related_name="medication_errors")
    medication = models.CharField(max_length=200)
    stage = models.CharField(max_length=16, choices=Stage.choices)
    error_type = models.CharField(max_length=16, choices=ErrorType.choices)
    category = models.CharField(max_length=1, choices=Category.choices, default=Category.C)
    prescribed = models.CharField(max_length=200, blank=True, help_text="What was intended.")
    actual = models.CharField(max_length=200, blank=True, help_text="What actually happened.")
    description = models.TextField(blank=True)
    detected_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    occurred_at = models.DateTimeField(default=timezone.now)
    incident = models.OneToOneField(SafetyIncident, on_delete=models.SET_NULL, null=True, blank=True, related_name="medication_error")

    class Meta:
        ordering = ["-occurred_at"]

    @property
    def is_near_miss(self):
        return self.category in ("A", "B")


class EmergencyCode(TenantScopedModel):
    """COP.4.d code definitions — Blue (cardiac arrest), Red (fire), Pink
    (infant abduction), Yellow (mass casualty), Grey (violence), etc."""

    code = models.CharField(max_length=30)
    color = models.CharField(max_length=20)
    meaning = models.CharField(max_length=200)
    protocol = models.TextField(blank=True)
    responder_roles = models.JSONField(default=list, blank=True, help_text="Role templates alerted, e.g. ['doctor','nurse','icu_staff'].")
    responders = models.ManyToManyField(USER, blank=True, related_name="+")
    target_response_minutes = models.PositiveSmallIntegerField(default=5)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class CodeActivation(TenantScopedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"

    code = models.ForeignKey(EmergencyCode, on_delete=models.PROTECT, related_name="activations")
    location = models.CharField(max_length=150)
    patient = models.ForeignKey("patients.Patient", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    activated_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="+")
    activated_at = models.DateTimeField(default=timezone.now)
    is_drill = models.BooleanField(default=False)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ACTIVE)
    closed_at = models.DateTimeField(null=True, blank=True)
    outcome = models.TextField(blank=True)

    class Meta:
        ordering = ["-activated_at"]


class CodeResponse(TenantScopedModel):
    activation = models.ForeignKey(CodeActivation, on_delete=models.CASCADE, related_name="responses")
    staff = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="+")
    role_in_response = models.CharField(max_length=60, blank=True)
    responded_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["responded_at"]
        constraints = [models.UniqueConstraint(fields=["activation", "staff"], name="one_response_per_staff_per_activation")]


class MockDrill(TenantScopedModel):
    code = models.ForeignKey(EmergencyCode, on_delete=models.SET_NULL, null=True, blank=True, related_name="drills")
    drill_date = models.DateField(default=timezone.localdate)
    location = models.CharField(max_length=150, blank=True)
    variations_observed = models.PositiveSmallIntegerField(default=0)
    observations = models.TextField(blank=True)
    corrective_actions = models.TextField(blank=True)
    conducted_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-drill_date"]


class ChecklistTemplate(TenantScopedModel):
    """MOM.4.d (emergency medication protocol / crash-cart / stock audit
    checklists) and any other reusable checklist. items: [{text, required}]."""

    name = models.CharField(max_length=150)
    purpose = models.CharField(max_length=40, default="general", help_text="crash_cart | stock_audit | emergency_protocol | administration | general")
    items = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ChecklistRun(TenantScopedModel):
    template = models.ForeignKey(ChecklistTemplate, on_delete=models.PROTECT, related_name="runs")
    location = models.CharField(max_length=150, blank=True)
    responses = models.JSONField(default=list, help_text="[{text, checked, remark}] aligned to template.items.")
    completed_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    completed_at = models.DateTimeField(default=timezone.now)
    all_passed = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ["-completed_at"]

    def save(self, *args, **kwargs):
        self.all_passed = bool(self.responses) and all(r.get("checked") for r in self.responses)
        super().save(*args, **kwargs)


class KPIManualEntry(TenantScopedModel):
    """Audit-based indicators NABH itself says may be entered manually
    ("provision for entering the manual/electronically collected data")."""

    kpi_code = models.CharField(max_length=10)
    period_start = models.DateField()
    period_end = models.DateField()
    numerator = models.DecimalField(max_digits=14, decimal_places=2)
    denominator = models.DecimalField(max_digits=14, decimal_places=2)
    notes = models.TextField(blank=True)
    entered_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-period_start", "kpi_code"]


class KPISnapshot(TenantScopedModel):
    """A computed (or manual) KPI value for a period — the published record
    (IMS.2.c quarterly publishing)."""

    kpi_code = models.CharField(max_length=10)
    kpi_name = models.CharField(max_length=200)
    period_start = models.DateField()
    period_end = models.DateField()
    numerator = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    denominator = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    value = models.DecimalField(max_digits=14, decimal_places=4, null=True)
    unit = models.CharField(max_length=30)
    source = models.CharField(max_length=10, default="system", help_text="system | manual | unavailable")
    note = models.CharField(max_length=300, blank=True)
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["period_start", "kpi_code"]
        constraints = [models.UniqueConstraint(fields=["hospital", "kpi_code", "period_start", "period_end"], name="unique_kpi_snapshot_per_period")]


class SystemHeartbeat(models.Model):
    """DHS uptime KPI — a Celery beat task writes one row every
    INTERVAL_SECONDS while the application stack (web DB + worker) is up;
    uptime % = observed / expected beats."""

    INTERVAL_SECONDS = 300
    beat_at = models.DateTimeField(auto_now_add=True, db_index=True)
    db_latency_ms = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-beat_at"]
