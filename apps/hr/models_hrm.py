"""NABH HRM chapter additions: staff master details (HRM.1.a), duty rules
(HRM.1.c), roster publishing (HRM.1.d/e), appraisals (HRM.1.h), payroll
(HRM.1.i), recruitment (HRM.2.a), exit (HRM.2.b), induction & training
(HRM.3.a–c). Imported by hr.models."""
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel

USER = settings.AUTH_USER_MODEL


class StaffProfile(TenantScopedModel):
    """HRM.1.a professional & personal master data beyond hr.Employee."""

    employee = models.OneToOneField("hr.Employee", on_delete=models.CASCADE, related_name="profile")
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    emergency_contact = models.CharField(max_length=150, blank=True)
    qualifications = models.JSONField(default=list, blank=True, help_text="[{degree, institute, year}]")
    council_registration = models.CharField(max_length=60, blank=True, help_text="MCI/NMC/State nursing council number")
    registration_valid_until = models.DateField(null=True, blank=True)
    credentials_verified = models.BooleanField(default=False)
    privileges = models.TextField(blank=True, help_text="Clinical privileges granted (for doctors).")
    immunisation = models.JSONField(default=dict, blank=True, help_text='{"hepatitis_b": "3 doses", "covid": "2 doses"}')
    uan = models.CharField(max_length=20, blank=True, help_text="PF UAN")
    esi_number = models.CharField(max_length=20, blank=True)


class DutyRule(TenantScopedModel):
    """HRM.1.c — checked when a roster is published."""

    name = models.CharField(max_length=100)
    applies_to_designation = models.CharField(max_length=100, blank=True, help_text="Blank = all staff; else substring, e.g. 'nurse'.")
    max_shifts_per_week = models.PositiveSmallIntegerField(default=6)
    max_consecutive_nights = models.PositiveSmallIntegerField(default=3)
    min_rest_hours_between_shifts = models.PositiveSmallIntegerField(default=12)
    weekly_off_required = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)


class RosterPublication(TenantScopedModel):
    """HRM.1.d/e — a roster period is published (locked) and every rostered
    staff member is notified of their shifts."""

    department = models.ForeignKey("core.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    period_start = models.DateField()
    period_end = models.DateField()
    published_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="+")
    published_at = models.DateTimeField(default=timezone.now)
    violations = models.JSONField(default=list, blank=True)
    notified_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-period_start"]


class SalaryStructure(TenantScopedModel):
    """Monthly CTC components. Statutory deductions follow Indian rules:
    PF 12% of basic (capped at ₹15,000 basic unless opted out), ESI 0.75%
    employee when gross ≤ ₹21,000, professional tax by state slab."""

    employee = models.OneToOneField("hr.Employee", on_delete=models.CASCADE, related_name="salary_structure")
    basic = models.DecimalField(max_digits=10, decimal_places=2)
    hra = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    special_allowance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    other_allowances = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    pf_applicable = models.BooleanField(default=True)
    pf_on_full_basic = models.BooleanField(default=False)
    esi_applicable = models.BooleanField(default=True)
    professional_tax = models.DecimalField(max_digits=8, decimal_places=2, default=200, help_text="Monthly PT (Maharashtra: ₹200; ₹300 in February).")
    monthly_tds = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    effective_from = models.DateField(default=timezone.localdate)

    @property
    def gross(self):
        return self.basic + self.hra + self.special_allowance + self.other_allowances


class PayrollRun(TenantScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        PAID = "paid", "Paid"

    month = models.DateField(help_text="First day of the payroll month.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    processed_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="+")
    approved_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    total_gross = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_net = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ["-month"]
        constraints = [models.UniqueConstraint(fields=["hospital", "month"], name="one_payroll_run_per_month")]


def _r(x):
    return Decimal(x).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


class Payslip(TenantScopedModel):
    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="payslips")
    employee = models.ForeignKey("hr.Employee", on_delete=models.PROTECT, related_name="payslips")
    days_in_month = models.PositiveSmallIntegerField()
    paid_days = models.DecimalField(max_digits=5, decimal_places=1)
    earnings = models.JSONField(default=dict)
    deductions = models.JSONField(default=dict)
    employer_contributions = models.JSONField(default=dict)
    gross = models.DecimalField(max_digits=12, decimal_places=2)
    total_deductions = models.DecimalField(max_digits=12, decimal_places=2)
    net_pay = models.DecimalField(max_digits=12, decimal_places=2)
    shared_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["employee__employee_code"]
        constraints = [models.UniqueConstraint(fields=["run", "employee"], name="one_payslip_per_employee_per_run")]

    @classmethod
    def compute(cls, structure, days_in_month, paid_days, month):
        f = Decimal(paid_days) / Decimal(days_in_month)
        earnings = {k: _r(getattr(structure, k) * f) for k in ("basic", "hra", "special_allowance", "other_allowances")}
        gross = sum(earnings.values(), Decimal("0"))
        pf_base = earnings["basic"] if structure.pf_on_full_basic else min(earnings["basic"], Decimal("15000") * f)
        pf = _r(pf_base * Decimal("0.12")) if structure.pf_applicable else Decimal("0")
        esi = _r(gross * Decimal("0.0075")) if structure.esi_applicable and structure.gross <= 21000 else Decimal("0")
        esi_er = _r(gross * Decimal("0.0325")) if esi else Decimal("0")
        pt = Decimal("300") if month.month == 2 and structure.professional_tax == 200 else structure.professional_tax
        pt = pt if gross > 7500 else Decimal("0")
        deductions = {"pf": pf, "esi": esi, "professional_tax": pt, "tds": structure.monthly_tds}
        total_ded = sum(deductions.values(), Decimal("0"))
        employer = {"pf": pf, "esi": esi_er}
        return {
            "earnings": {k: str(v) for k, v in earnings.items()}, "deductions": {k: str(v) for k, v in deductions.items()},
            "employer_contributions": {k: str(v) for k, v in employer.items()}, "gross": gross, "total_deductions": total_ded, "net_pay": gross - total_ded,
        }


class Appraisal(TenantScopedModel):
    """HRM.1.h. ratings: {competency: 1–5}; overall is their mean."""

    employee = models.ForeignKey("hr.Employee", on_delete=models.CASCADE, related_name="appraisals")
    period = models.CharField(max_length=20, help_text="e.g. FY2025-26")
    ratings = models.JSONField(default=dict)
    overall_rating = models.DecimalField(max_digits=3, decimal_places=2, null=True, editable=False)
    strengths = models.TextField(blank=True)
    improvement_areas = models.TextField(blank=True)
    goals = models.TextField(blank=True)
    appraiser = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="+")
    employee_acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["employee", "period"], name="one_appraisal_per_period")]

    def save(self, *args, **kwargs):
        nums = [float(v) for v in self.ratings.values() if str(v).replace(".", "", 1).isdigit()]
        self.overall_rating = round(sum(nums) / len(nums), 2) if nums else None
        super().save(*args, **kwargs)


class JobOpening(TenantScopedModel):
    """HRM.2.a"""

    title = models.CharField(max_length=150)
    department = models.ForeignKey("core.Department", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    positions = models.PositiveSmallIntegerField(default=1)
    requirements = models.TextField(blank=True)
    approval_status = models.CharField(max_length=10, default="pending", help_text="pending | approved | rejected")
    approved_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=10, default="open", help_text="open | closed")

    class Meta:
        ordering = ["-created_at"]


class Candidate(TenantScopedModel):
    STAGES = ["applied", "screened", "interview", "offered", "joined", "rejected"]

    opening = models.ForeignKey(JobOpening, on_delete=models.CASCADE, related_name="candidates")
    name = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    resume = models.FileField(upload_to="resumes/", blank=True)
    stage = models.CharField(max_length=10, default="applied")
    interview_feedback = models.JSONField(default=list, blank=True, help_text="[{interviewer, rating, notes}]")
    credentials_verified = models.BooleanField(default=False)
    police_verification_done = models.BooleanField(default=False)
    medical_fitness_done = models.BooleanField(default=False)
    offer_ctc = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class ExitRequest(TenantScopedModel):
    """HRM.2.b — resignation → clearances → exit interview → F&F."""

    employee = models.ForeignKey("hr.Employee", on_delete=models.CASCADE, related_name="exit_requests")
    reason = models.TextField()
    resignation_date = models.DateField(default=timezone.localdate)
    last_working_day = models.DateField()
    clearances = models.JSONField(default=dict, help_text='{"it": false, "stores": false, "finance": false, "department": false, "library": false}')
    exit_interview_notes = models.TextField(blank=True)
    full_and_final_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=12, default="initiated", help_text="initiated | cleared | completed")

    class Meta:
        ordering = ["-resignation_date"]

    DEFAULT_CLEARANCES = ["it", "stores", "finance", "department", "hr"]


class TrainingProgram(TenantScopedModel):
    """HRM.3.b/c training calendar. kind 'induction' = HRM.3.a."""

    title = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, default="in_service", help_text="induction | in_service | mandatory | cme | fire_safety | bls")
    trainer = models.CharField(max_length=150, blank=True)
    scheduled_at = models.DateTimeField()
    duration_hours = models.DecimalField(max_digits=4, decimal_places=1, default=1)
    venue = models.CharField(max_length=150, blank=True)
    target_designations = models.CharField(max_length=200, blank=True)
    invitees = models.ManyToManyField("hr.Employee", blank=True, related_name="training_invites")

    class Meta:
        ordering = ["scheduled_at"]


class TrainingAttendance(TenantScopedModel):
    program = models.ForeignKey(TrainingProgram, on_delete=models.CASCADE, related_name="attendance")
    employee = models.ForeignKey("hr.Employee", on_delete=models.CASCADE, related_name="trainings")
    attended = models.BooleanField(default=True)
    pre_test_score = models.PositiveSmallIntegerField(null=True, blank=True)
    post_test_score = models.PositiveSmallIntegerField(null=True, blank=True)
    feedback_rating = models.PositiveSmallIntegerField(null=True, blank=True)
    feedback = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["program", "employee"], name="unique_training_attendance")]
