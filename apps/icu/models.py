from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.appointments.models import Doctor
from apps.core.models import FinalizableModel, TenantScopedModel
from apps.facilities.models import Bed
from apps.ipd.models import Admission


class ICUAdmission(TenantScopedModel):
    admission = models.OneToOneField(Admission, on_delete=models.CASCADE, related_name="icu_admission")
    bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="icu_admissions")
    ventilator_required = models.BooleanField(default=False)
    admitted_at = models.DateTimeField(default=timezone.now)
    discharged_at = models.DateTimeField(null=True, blank=True)

    # COP.5.a criteria-based admission/discharge decisions.
    admission_criteria_met = models.JSONField(default=list, blank=True, help_text="Codes of ICUCriterion rows (kind=admission) met.")
    is_eligible = models.BooleanField(null=True, editable=False)
    decision_reason = models.TextField(blank=True)
    discharge_criteria_met = models.JSONField(default=list, blank=True)
    # COP.5.b severity & outcome (drives the ICU SMR KPI).
    severity_scale = models.CharField(max_length=12, blank=True, help_text="apache_ii | sofa")
    severity_inputs = models.JSONField(default=dict, blank=True)
    severity_score = models.PositiveSmallIntegerField(null=True, blank=True, editable=False)
    predicted_mortality = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True, editable=False, help_text="%")

    class Outcome(models.TextChoices):
        WARD = "ward", "Shifted to ward"
        TRANSFERRED = "transferred", "Transferred out"
        DAMA = "dama", "Discharged against advice"
        DEATH = "death", "Death"

    outcome = models.CharField(max_length=12, choices=Outcome.choices, blank=True)
    is_readmission_48h = models.BooleanField(default=False, editable=False)

    def save(self, *args, **kwargs):
        from .scoring import compute

        if self.severity_scale and self.severity_inputs:
            self.severity_score, self.predicted_mortality = compute(self.severity_scale, self.severity_inputs)
        # None = criteria not yet recorded; True once at least one active
        # admission criterion is met.
        self.is_eligible = True if self.admission_criteria_met else None
        if not self.pk and self.admission_id:
            from datetime import timedelta

            self.is_readmission_48h = ICUAdmission.objects.filter(
                admission__patient_id=self.admission.patient_id, discharged_at__gte=self.admitted_at - timedelta(hours=48),
            ).exists()
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["-admitted_at"]
        # No `status` field here (unlike ipd.Admission) — "currently in
        # ICU" is discharged_at IS NULL, the field this index targets.
        indexes = [models.Index(fields=["hospital", "discharged_at"])]

    def __str__(self):
        return f"ICU Admission for {self.admission.patient} (Bed: {self.bed.bed_number})"


class VentilatorLog(TenantScopedModel):
    icu_admission = models.ForeignKey(ICUAdmission, on_delete=models.CASCADE, related_name="ventilator_logs")
    mode = models.CharField(max_length=64)
    ventilator_settings = models.JSONField(default=dict, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-recorded_at"]

    def __str__(self):
        return f"Ventilator Log ({self.mode}) - {self.icu_admission}"


class ICUDailyProgressNote(FinalizableModel, TenantScopedModel):
    FINALIZED_LOCKED_FIELDS = ("note",)

    icu_admission = models.ForeignKey(ICUAdmission, on_delete=models.CASCADE, related_name="progress_notes")
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="icu_progress_notes")
    note = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("finalize_icudailyprogressnote", "Can finalize an ICU daily progress note"),
        ]

    def __str__(self):
        return f"ICU Note by Dr. {self.doctor} for {self.icu_admission}"


class ICUCriterion(TenantScopedModel):
    """Configurable, evidence-based ICU admission/discharge criteria."""

    class Kind(models.TextChoices):
        ADMISSION = "admission", "Admission"
        DISCHARGE = "discharge", "Discharge"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    code = models.CharField(max_length=40)
    description = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "code"]


class CareBundleLog(TenantScopedModel):
    """COP.5.d — daily care-bundle documentation (VAP, CLABSI, CAUTI,
    pressure-injury). compliant = every element done."""

    BUNDLES = {
        "vap": ["head_elevated_30_45", "daily_sedation_vacation", "oral_care_chlorhexidine", "dvt_prophylaxis", "pud_prophylaxis", "cuff_pressure_checked"],
        "clabsi": ["hand_hygiene", "dressing_intact_dated", "daily_need_reviewed", "hub_scrubbed", "aseptic_access"],
        "cauti": ["daily_need_reviewed", "closed_drainage", "bag_below_bladder", "perineal_care", "securement"],
        "pressure_injury": ["skin_inspected", "repositioned_2_hourly", "pressure_relief_mattress", "nutrition_reviewed", "moisture_managed"],
    }

    icu_admission = models.ForeignKey(ICUAdmission, on_delete=models.CASCADE, related_name="bundle_logs")
    bundle = models.CharField(max_length=20)
    elements = models.JSONField(default=dict)
    compliant = models.BooleanField(default=False, editable=False)
    logged_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    logged_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-logged_at"]

    def save(self, *args, **kwargs):
        required = self.BUNDLES.get(self.bundle, [])
        self.compliant = bool(required) and all(self.elements.get(e) for e in required)
        super().save(*args, **kwargs)
