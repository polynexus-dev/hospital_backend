"""Front-desk fee and billing rules, set by hospital administrators (finance
permissions), so the counter can't change its own fees or limits."""
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import TenantScopedModel


class ConsultationFeeRule(TenantScopedModel):
    """What an OPD consultation costs and when a follow-up is free. The most
    specific active rule wins: the doctor's own, else the department's,
    else the hospital default (no doctor, no department)."""

    doctor = models.ForeignKey("appointments.Doctor", on_delete=models.CASCADE, null=True, blank=True, related_name="fee_rules")
    department = models.ForeignKey("core.Department", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    new_visit_tariff = models.ForeignKey("finance.ServiceTariff", on_delete=models.PROTECT, related_name="+")
    follow_up_tariff = models.ForeignKey(
        "finance.ServiceTariff", on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        help_text="Charged for a paid follow-up; blank = the new-visit fee.",
    )
    free_follow_up_days = models.PositiveSmallIntegerField(default=7, help_text="Days after a paid visit within which follow-ups are free.")
    max_free_follow_ups = models.PositiveSmallIntegerField(default=1, help_text="Free follow-ups allowed per paid visit.")
    follow_up_valid_days = models.PositiveSmallIntegerField(default=30, help_text="After this many days a visit counts as new again.")
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "finance"
        ordering = ["-doctor", "-department"]

    def __str__(self):
        scope = f"Dr. {self.doctor.name}" if self.doctor_id else self.department.name if self.department_id else "Hospital default"
        return f"Fee rule: {scope}"

    @classmethod
    def for_visit(cls, hospital_id, doctor):
        rules = cls.objects.filter(hospital_id=hospital_id, is_active=True).select_related("new_visit_tariff", "follow_up_tariff")
        return (
            rules.filter(doctor=doctor).first()
            or (rules.filter(doctor__isnull=True, department_id=doctor.department_id).first() if doctor.department_id else None)
            or rules.filter(doctor__isnull=True, department__isnull=True).first()
        )


class FrontDeskPolicy(TenantScopedModel):
    """One per hospital: registration fee, discount limit, credit and rounding."""

    registration_fee_tariff = models.ForeignKey(
        "finance.ServiceTariff", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Charged once when a new patient is registered (blank = no registration fee).",
    )
    discount_limit_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("10"), validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Discounts above this percent of the bill need an authorised approver.",
    )
    credit_payer_types = models.JSONField(
        default=list, blank=True,
        help_text='Payer types that may be billed on credit, e.g. ["corporate", "tpa", "insurance", "scheme"]. Others pay at the counter.',
    )
    round_down_to_rupee = models.BooleanField(default=True)
    default_rate_category = models.CharField(max_length=20, default="general")

    class Meta:
        app_label = "finance"
        constraints = [models.UniqueConstraint(fields=["hospital"], name="one_front_desk_policy_per_hospital")]

    DEFAULT_CREDIT_PAYERS = ["corporate", "tpa", "insurance", "scheme"]

    @classmethod
    def for_hospital(cls, hospital_id):
        policy, _ = cls.objects.get_or_create(hospital_id=hospital_id, defaults={"credit_payer_types": cls.DEFAULT_CREDIT_PAYERS})
        return policy
