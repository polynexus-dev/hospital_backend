"""Doctor payouts — revenue share / professional fee for services a doctor rendered."""
from django.conf import settings
from django.db import models

from apps.core.models import TenantScopedModel


class DoctorPayoutRule(TenantScopedModel):
    """How much of a bill line a doctor earns. Blank criteria match anything;
    the most specific matching rule wins (doctor > service > service
    department > patient category > doctor's department), then `priority`.

    `earned_on`: "billed" pays on services billed in the period; "collected"
    only once the bill is fully paid (dated by the last payment)."""

    class Basis(models.TextChoices):
        PERCENT = "percent", "% of net amount (after discount, before GST)"
        FIXED = "fixed", "Fixed amount per unit"

    class EarnedOn(models.TextChoices):
        BILLED = "billed", "When billed"
        COLLECTED = "collected", "When the bill is fully paid"

    name = models.CharField(max_length=150)
    doctor = models.ForeignKey("appointments.Doctor", on_delete=models.CASCADE, null=True, blank=True, related_name="payout_rules")
    doctor_department = models.ForeignKey("core.Department", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    tariff = models.ForeignKey("finance.ServiceTariff", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    service_department = models.CharField(max_length=60, blank=True, help_text="ServiceTariff.department, e.g. 'Radiology'")
    patient_category = models.CharField(max_length=12, blank=True, help_text="general | private | insurance | corporate; blank = any")
    basis = models.CharField(max_length=8, choices=Basis.choices, default=Basis.PERCENT)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    earned_on = models.CharField(max_length=10, choices=EarnedOn.choices, default=EarnedOn.BILLED)
    priority = models.SmallIntegerField(default=0)
    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "finance"
        ordering = ["-priority", "name"]

    def __str__(self):
        return self.name


class DoctorPayout(TenantScopedModel):
    """A payout statement: one doctor, one period."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    doctor = models.ForeignKey("appointments.Doctor", on_delete=models.PROTECT, related_name="payouts")
    period_start = models.DateField()
    period_end = models.DateField()
    base_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text="Net billed value of the services")
    gross_payout = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tds_percent = models.DecimalField(max_digits=5, decimal_places=2, default=10, help_text="Sec 194J professional fees")
    tds_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    net_payable = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_on = models.DateField(null=True, blank=True)
    payment_mode = models.CharField(max_length=10, blank=True, help_text="neft | upi | cheque | cash")
    payment_reference = models.CharField(max_length=60, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        app_label = "finance"
        ordering = ["-period_end", "doctor__name"]


class DoctorPayoutLine(models.Model):
    """One bill line on a statement. `bill_item` is unique, so a service can
    only ever be on one live statement; cancelling a statement deletes its
    lines and frees the items for the next run."""

    payout = models.ForeignKey(DoctorPayout, on_delete=models.CASCADE, related_name="lines")
    bill_item = models.OneToOneField("billing.BillItem", on_delete=models.SET_NULL, null=True, related_name="payout_line")
    rule = models.ForeignKey(DoctorPayoutRule, on_delete=models.SET_NULL, null=True, related_name="+")
    rule_label = models.CharField(max_length=200)
    bill_number = models.CharField(max_length=30)
    patient_name = models.CharField(max_length=255)
    description = models.CharField(max_length=255)
    service_date = models.DateField()
    quantity = models.PositiveIntegerField(default=1)
    base_amount = models.DecimalField(max_digits=12, decimal_places=2)
    payout_amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        app_label = "finance"
        ordering = ["service_date", "id"]
