"""
Cancer care & oncology — NABH Annexure "Cancer Care and Oncology" for
HIS/EMR (COP.1 oncology enhancements, MDC.1 tumour board, MDC.2 clinical
trials, MDC.3 bone-marrow transplant, IMS.1.e ICD-O-3).
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel

USER = settings.AUTH_USER_MODEL


class CancerCase(TenantScopedModel):
    class Basis(models.TextChoices):
        CLINICAL = "clinical", "Clinical only"
        IMAGING = "imaging", "Imaging"
        CYTOLOGY = "cytology", "Cytology"
        HISTOPATHOLOGY = "histopathology", "Histopathology of primary"
        MOLECULAR = "molecular", "Molecular / genetic"

    class Intent(models.TextChoices):
        CURATIVE = "curative", "Curative"
        PALLIATIVE = "palliative", "Palliative"

    class Status(models.TextChoices):
        WORKUP = "workup", "Work-up"
        ON_TREATMENT = "on_treatment", "On treatment"
        SURVEILLANCE = "surveillance", "Follow-up / surveillance"
        RECURRENCE = "recurrence", "Recurrence"
        PALLIATIVE = "palliative", "Palliative care"
        DECEASED = "deceased", "Deceased"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="cancer_cases")
    episode = models.ForeignKey("clinical.EpisodeOfCare", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    primary_site = models.CharField(max_length=150)
    laterality = models.CharField(max_length=10, blank=True, help_text="left | right | bilateral | na")
    icd10_code = models.CharField(max_length=10, blank=True)
    icdo3_topography = models.CharField(max_length=10, blank=True, help_text="e.g. C50.4")
    icdo3_morphology = models.CharField(max_length=12, blank=True, help_text="e.g. 8500/3")
    histology = models.CharField(max_length=200, blank=True)
    grade = models.CharField(max_length=10, blank=True)
    diagnosis_date = models.DateField(default=timezone.localdate)
    basis_of_diagnosis = models.CharField(max_length=16, choices=Basis.choices, default=Basis.HISTOPATHOLOGY)
    staging_system = models.CharField(max_length=30, default="AJCC 8th")
    t_stage = models.CharField(max_length=6, blank=True)
    n_stage = models.CharField(max_length=6, blank=True)
    m_stage = models.CharField(max_length=6, blank=True)
    stage_group = models.CharField(max_length=10, blank=True)
    biomarkers = models.JSONField(default=dict, blank=True, help_text='{"ER": "+", "PR": "-", "HER2": "3+", "EGFR": "exon19del"}')
    ecog = models.PositiveSmallIntegerField(null=True, blank=True)
    treatment_intent = models.CharField(max_length=10, choices=Intent.choices, default=Intent.CURATIVE)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.WORKUP)
    treating_oncologist = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    date_of_death = models.DateField(null=True, blank=True)
    cause_of_death = models.CharField(max_length=255, blank=True)
    death_within_30_days_of_treatment = models.BooleanField(default=False, editable=False)
    death_related_modality = models.CharField(max_length=20, blank=True, help_text="surgery | chemotherapy | radiotherapy")

    class Meta:
        ordering = ["-diagnosis_date"]

    @property
    def tnm(self):
        return "".join(x for x in (f"T{self.t_stage}" if self.t_stage else "", f"N{self.n_stage}" if self.n_stage else "", f"M{self.m_stage}" if self.m_stage else ""))

    def last_treatment_date(self):
        dates = [d for d in (
            self.chemo_cycles.filter(given_on__isnull=False).order_by("-given_on").values_list("given_on", flat=True).first(),
            self.radiotherapy_plans.filter(end_date__isnull=False).order_by("-end_date").values_list("end_date", flat=True).first(),
            self.surgeries.order_by("-surgery_date").values_list("surgery_date", flat=True).first(),
        ) if d]
        return max(dates) if dates else None


class SurgicalOncologyRecord(TenantScopedModel):
    case = models.ForeignKey(CancerCase, on_delete=models.CASCADE, related_name="surgeries")
    ot_schedule = models.ForeignKey("ot.OTSchedule", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    surgery_date = models.DateField()
    procedure_name = models.CharField(max_length=200)
    extent = models.CharField(max_length=20, blank=True, help_text="curative | debulking | palliative | biopsy")
    frozen_section = models.TextField(blank=True)
    hpr_findings = models.TextField(blank=True, help_text="Histopathology report findings")
    margins = models.CharField(max_length=20, blank=True, help_text="R0 | R1 | R2")
    lymph_nodes = models.CharField(max_length=40, blank=True, help_text="e.g. 3/18 positive")
    pathological_stage = models.CharField(max_length=20, blank=True, help_text="pTNM")


class ChemoProtocol(TenantScopedModel):
    """Regimen template. drugs: [{drug, dose, unit: mg/m2|mg/kg|mg|AUC, route, day}]."""

    name = models.CharField(max_length=100)
    indication = models.CharField(max_length=150, blank=True)
    drugs = models.JSONField(default=list)
    cycle_length_days = models.PositiveSmallIntegerField(default=21)
    planned_cycles = models.PositiveSmallIntegerField(default=6)
    emetogenic_risk = models.CharField(max_length=10, blank=True)
    premedications = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


def mosteller_bsa(height_cm, weight_kg):
    return round(((float(height_cm) * float(weight_kg)) / 3600) ** 0.5, 2)


class ChemoCycle(TenantScopedModel):
    class Indication(models.TextChoices):
        NEOADJUVANT = "neoadjuvant", "Neoadjuvant"
        ADJUVANT = "adjuvant", "Adjuvant"
        CONCURRENT = "concurrent", "Concurrent chemoradiation"
        PALLIATIVE = "palliative", "Palliative"
        DEFINITIVE = "definitive", "Definitive"

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        GIVEN = "given", "Given"
        DELAYED = "delayed", "Delayed"
        CANCELLED = "cancelled", "Cancelled"

    case = models.ForeignKey(CancerCase, on_delete=models.CASCADE, related_name="chemo_cycles")
    protocol = models.ForeignKey(ChemoProtocol, on_delete=models.PROTECT, related_name="cycles")
    cycle_number = models.PositiveSmallIntegerField()
    indication = models.CharField(max_length=12, choices=Indication.choices)
    planned_on = models.DateField()
    given_on = models.DateField(null=True, blank=True)
    height_cm = models.DecimalField(max_digits=5, decimal_places=1)
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1)
    bsa = models.DecimalField(max_digits=4, decimal_places=2, editable=False, default=0)
    dose_reduction_pct = models.PositiveSmallIntegerField(default=0)
    calculated_doses = models.JSONField(default=list, editable=False)
    pre_chemo_labs = models.JSONField(default=dict, blank=True, help_text='{"ANC": 1.8, "platelets": 150, "creatinine": 0.9}')
    fit_for_chemo = models.BooleanField(null=True)
    toxicities = models.JSONField(default=list, blank=True, help_text="[{toxicity, ctcae_grade}] — CTCAE v5")
    response = models.CharField(max_length=20, blank=True, help_text="CR | PR | SD | PD (RECIST)")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PLANNED)
    delay_reason = models.CharField(max_length=255, blank=True)
    prescribed_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="+")
    verified_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", help_text="Independent double-check of doses.")

    class Meta:
        ordering = ["case", "cycle_number"]

    def save(self, *args, **kwargs):
        self.bsa = Decimal(str(mosteller_bsa(self.height_cm, self.weight_kg)))
        factor = (100 - self.dose_reduction_pct) / 100
        doses = []
        for d in self.protocol.drugs:
            unit = d.get("unit", "mg")
            base = float(d.get("dose", 0))
            if unit == "mg/m2":
                amount = base * float(self.bsa)
            elif unit == "mg/kg":
                amount = base * float(self.weight_kg)
            else:
                amount = base
            doses.append({**d, "calculated_mg": round(amount * factor, 1)})
        self.calculated_doses = doses
        super().save(*args, **kwargs)


class RadiotherapyPlan(TenantScopedModel):
    case = models.ForeignKey(CancerCase, on_delete=models.CASCADE, related_name="radiotherapy_plans")
    site = models.CharField(max_length=150)
    technique = models.CharField(max_length=30, help_text="3DCRT | IMRT | VMAT | SBRT | brachytherapy")
    intent = models.CharField(max_length=10, choices=CancerCase.Intent.choices)
    indication = models.CharField(max_length=12, choices=ChemoCycle.Indication.choices, blank=True)
    total_dose_gy = models.DecimalField(max_digits=6, decimal_places=2)
    fractions = models.PositiveSmallIntegerField()
    simulation_date = models.DateField(null=True, blank=True)
    ct_scan_details = models.TextField(blank=True)
    positioning = models.CharField(max_length=150, blank=True)
    immobilisation = models.CharField(max_length=150, blank=True, help_text="thermoplastic mask, vac-lok ...")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    toxicities = models.JSONField(default=list, blank=True)

    @property
    def dose_per_fraction(self):
        return round(float(self.total_dose_gy) / self.fractions, 2) if self.fractions else None


class RadiotherapyFraction(TenantScopedModel):
    plan = models.ForeignKey(RadiotherapyPlan, on_delete=models.CASCADE, related_name="delivered_fractions")
    fraction_number = models.PositiveSmallIntegerField()
    delivered_on = models.DateField(default=timezone.localdate)
    dose_gy = models.DecimalField(max_digits=5, decimal_places=2)
    remarks = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["fraction_number"]
        constraints = [models.UniqueConstraint(fields=["plan", "fraction_number"], name="unique_rt_fraction")]


class TumorBoardMeeting(TenantScopedModel):
    """MDC.1.a–d multidisciplinary tumour board."""

    board_id = models.CharField(max_length=30, editable=False)
    title = models.CharField(max_length=150, default="Multidisciplinary Tumour Board")
    scheduled_at = models.DateTimeField()
    location = models.CharField(max_length=150, blank=True, help_text="Room or video link")
    members = models.ManyToManyField(USER, blank=True, related_name="tumor_boards")
    status = models.CharField(max_length=10, default="scheduled", help_text="scheduled | held | cancelled")
    created_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        ordering = ["-scheduled_at"]

    def save(self, *args, **kwargs):
        if not self.board_id:
            n = TumorBoardMeeting.objects.filter(hospital_id=self.hospital_id).count() + 1
            self.board_id = f"MDTB-{timezone.localdate():%Y}-{n:04d}"
        super().save(*args, **kwargs)


class TumorBoardAttendance(TenantScopedModel):
    meeting = models.ForeignKey(TumorBoardMeeting, on_delete=models.CASCADE, related_name="attendance")
    member = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="+")
    designation = models.CharField(max_length=100, blank=True)
    specialty = models.CharField(max_length=100, blank=True, help_text="Surgical / Medical / Radiation oncology, Pathology, Radiology ...")
    role = models.CharField(max_length=60, blank=True)
    attended = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["meeting", "member"], name="unique_board_attendance")]


class TumorBoardCase(TenantScopedModel):
    meeting = models.ForeignKey(TumorBoardMeeting, on_delete=models.CASCADE, related_name="cases")
    case = models.ForeignKey(CancerCase, on_delete=models.CASCADE, related_name="board_reviews")
    presented_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="+")
    question = models.TextField(blank=True, help_text="What the board is asked to decide.")
    discussion_summary = models.TextField(blank=True)
    recommendation = models.TextField(blank=True)
    treatment_plan = models.CharField(max_length=200, blank=True, help_text="e.g. NACT → surgery → RT")
    follow_up = models.TextField(blank=True)
    is_consensus = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["meeting", "case"], name="unique_case_per_board")]


class ClinicalTrial(TenantScopedModel):
    """MDC.2.a/b"""

    trial_id = models.CharField(max_length=60, help_text="CTRI registration number")
    title = models.CharField(max_length=300)
    phase = models.CharField(max_length=10, blank=True)
    sponsor = models.CharField(max_length=150, blank=True)
    principal_investigator = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="+")
    arms = models.JSONField(default=list, help_text='["Arm A", "Arm B"] for randomisation')
    target_enrollment = models.PositiveIntegerField(default=0)
    ethics_approval_ref = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=12, default="recruiting", help_text="recruiting | active | closed | archived")
    investigational_product = models.CharField(max_length=200, blank=True)
    ip_stock = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.trial_id


class TrialEnrollment(TenantScopedModel):
    trial = models.ForeignKey(ClinicalTrial, on_delete=models.CASCADE, related_name="enrollments")
    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="trial_enrollments")
    subject_id = models.CharField(max_length=40, editable=False)
    consent = models.ForeignKey("clinical.ConsentRecord", on_delete=models.PROTECT, related_name="+")
    arm = models.CharField(max_length=60, blank=True, editable=False)
    enrolled_on = models.DateField(default=timezone.localdate)
    status = models.CharField(max_length=12, default="active", help_text="active | withdrawn | completed")
    visits = models.JSONField(default=list, blank=True, help_text="[{visit, due, done_on}]")
    adverse_events = models.JSONField(default=list, blank=True, help_text="[{event, ctcae_grade, serious, date}]")
    ip_dispensed = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["trial", "patient"], name="unique_trial_enrollment")]


class BoneMarrowTransplant(TenantScopedModel):
    """MDC.3.a — donor & recipient template."""

    class Kind(models.TextChoices):
        AUTOLOGOUS = "autologous", "Autologous"
        ALLOGENEIC = "allogeneic", "Allogeneic"

    recipient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="bmt_as_recipient")
    case = models.ForeignKey(CancerCase, on_delete=models.SET_NULL, null=True, blank=True, related_name="transplants")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    donor = models.ForeignKey("patients.Patient", on_delete=models.SET_NULL, null=True, blank=True, related_name="bmt_as_donor")
    donor_relation = models.CharField(max_length=60, blank=True, help_text="sibling / haplo / MUD / self")
    hla_match = models.CharField(max_length=20, blank=True, help_text="e.g. 10/10")
    recipient_blood_group = models.CharField(max_length=5, blank=True)
    donor_blood_group = models.CharField(max_length=5, blank=True)
    stem_cell_source = models.CharField(max_length=20, blank=True, help_text="PBSC | bone marrow | cord blood")
    cd34_dose = models.CharField(max_length=40, blank=True)
    conditioning_regimen = models.CharField(max_length=150, blank=True)
    committee_approval_ref = models.CharField(max_length=80, blank=True)
    consent = models.ForeignKey("clinical.ConsentRecord", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    transplant_date = models.DateField(null=True, blank=True)
    neutrophil_engraftment_date = models.DateField(null=True, blank=True)
    platelet_engraftment_date = models.DateField(null=True, blank=True)
    gvhd = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=14, default="work_up")
