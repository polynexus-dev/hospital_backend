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
    # MedNet-style "exclude medicine purchase cost": for an admission bill,
    # the purchase cost of medicines dispensed to the patient during the stay
    # is taken off the bill before the % is applied (shared across the bill's
    # lines like a discount). Fixed-amount rules are unaffected.
    deduct_medicine_cost = models.BooleanField(default=False, help_text="% rules only: share is on the bill net of medicine purchase cost (admission bills)")
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
    deductions_total = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text="Manual deduction heads (recoveries)")
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
    medicine_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Medicine purchase cost taken off this line's base")
    payout_amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        app_label = "finance"
        ordering = ["service_date", "id"]


class PayoutDeductionHead(TenantScopedModel):
    """A reason money is recovered from a consultant's payout: advance
    recovery, OT/room rent share, canteen, professional indemnity... Posted
    to its own ledger when the statement is paid."""

    name = models.CharField(max_length=100)
    ledger_code = models.CharField(max_length=10, default="4200", help_text="Ledger credited when paid (default 4200 Recoveries from consultants)")
    default_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    auto_apply = models.BooleanField(default=False, help_text="Add default_amount to every new statement")
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "finance"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["hospital", "name"], name="unique_payout_deduction_head")]

    def __str__(self):
        return self.name


class DoctorPayoutDeduction(models.Model):
    payout = models.ForeignKey(DoctorPayout, on_delete=models.CASCADE, related_name="deductions")
    head = models.ForeignKey(PayoutDeductionHead, on_delete=models.PROTECT, related_name="+")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    remarks = models.CharField(max_length=255, blank=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "finance"
        ordering = ["id"]


class PayoutStatementDelivery(TenantScopedModel):
    """One attempt to send a statement to the consultant."""

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        WHATSAPP = "whatsapp", "WhatsApp"

    payout = models.ForeignKey(DoctorPayout, on_delete=models.CASCADE, related_name="deliveries")
    channel = models.CharField(max_length=10, choices=Channel.choices)
    recipient = models.CharField(max_length=255)
    sent = models.BooleanField(default=False)
    error = models.CharField(max_length=255, blank=True)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        app_label = "finance"
        ordering = ["-created_at"]
