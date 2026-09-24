from django.conf import settings
from django.db import models
from django.utils import timezone
from apps.core.models import TenantScopedModel
from apps.ipd.models import Admission
from apps.patients.models import Patient


class Bill(TenantScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        UNPAID = "unpaid", "Unpaid"
        PARTIALLY_PAID = "partially_paid", "Partially Paid"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="bills")
    admission = models.ForeignKey(Admission, on_delete=models.SET_NULL, null=True, blank=True, related_name="bills")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    created_at = models.DateTimeField(default=timezone.now)
    # NABH AAC.6.e interim bills; FPM.3 GST invoice numbering.
    is_interim = models.BooleanField(default=False)
    bill_number = models.CharField(max_length=30, blank=True, editable=False)
    patient_category = models.CharField(max_length=12, default="general", help_text="general | private | insurance | corporate")
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    def save(self, *args, **kwargs):
        if not self.bill_number and self.hospital_id:
            prefix = "INT" if self.is_interim else "INV"
            n = Bill.objects.filter(hospital_id=self.hospital_id, bill_number__startswith=f"{prefix}{timezone.localdate():%y}").count() + 1
            self.bill_number = f"{prefix}{timezone.localdate():%y}{n:06d}"
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["hospital", "status"])]

    def __str__(self):
        return f"Bill #{self.id} for {self.patient} - ₹{self.net_amount} ({self.status})"


class BillItem(models.Model):
    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="items")
    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    total_price = models.DecimalField(max_digits=12, decimal_places=2)
    tariff = models.ForeignKey("finance.ServiceTariff", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    hsn_sac = models.CharField(max_length=10, blank=True)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Who rendered the service — drives doctor payouts (finance.DoctorPayoutRule).
    doctor = models.ForeignKey("appointments.Doctor", on_delete=models.SET_NULL, null=True, blank=True, related_name="bill_items")
    service_date = models.DateField(null=True, blank=True)
    # System-generated lines (e.g. "bed_charge") are re-derived on every
    # posting run and replaced wholesale; manual lines have source="".
    source = models.CharField(max_length=20, blank=True, db_index=True)
    source_ref = models.CharField(max_length=60, blank=True)

    def save(self, *args, **kwargs):
        from decimal import Decimal

        # unit_price can arrive as a request string — int * str would
        # repeat the string ("500" * 2 == "500500"), not multiply.
        self.unit_price = Decimal(str(self.unit_price))
        self.total_price = Decimal(self.quantity) * self.unit_price
        self.tax_amount = (Decimal(self.total_price) * Decimal(str(self.gst_rate or 0)) / Decimal("100")).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.description} ({self.quantity} x {self.unit_price})"


class BedChargeRule(TenantScopedModel):
    """Which tariff(s) a bed-day attracts. The most specific level wins:
    rules for the bed's ward, else rules for its bed type, else catch-all
    rules (no ward, no bed type). Several rules at the winning level are
    several daily components — e.g. room rent + nursing + RMO charges."""

    name = models.CharField(max_length=120)
    ward = models.ForeignKey("facilities.Ward", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    bed_type = models.CharField(max_length=16, blank=True, help_text="facilities.Bed.BedType value; blank = any")
    tariff = models.ForeignKey("finance.ServiceTariff", on_delete=models.PROTECT, related_name="+")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["ward__name", "bed_type", "name"]

    def __str__(self):
        return self.name


class BedBillingPolicy(TenantScopedModel):
    """How bed-days are counted. One per hospital; defaults apply until set."""

    class Cycle(models.TextChoices):
        TWENTY_FOUR_HOURS = "24h", "24-hour cycle from admission time"
        CALENDAR_DAY = "calendar_day", "Calendar day (discharge day charged only after checkout hour)"

    class TransferRule(models.TextChoices):
        HIGHER = "higher", "Charge the higher-rate bed for the day"
        LONGEST = "longest", "Charge the bed occupied longest that day"

    cycle = models.CharField(max_length=16, choices=Cycle.choices, default=Cycle.TWENTY_FOUR_HOURS)
    grace_hours = models.PositiveSmallIntegerField(default=2, help_text="24h cycle: hours into a new cycle before it is charged.")
    checkout_hour = models.PositiveSmallIntegerField(default=12, help_text="Calendar day: discharge after this hour charges the discharge day too.")
    transfer_day_rule = models.CharField(max_length=10, choices=TransferRule.choices, default=TransferRule.HIGHER)
    auto_post = models.BooleanField(default=True, help_text="Post bed charges nightly and at discharge.")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["hospital"], name="one_bed_billing_policy_per_hospital")]


class Payment(TenantScopedModel):
    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "card", "Card"
        UPI = "upi", "UPI"
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"
        CHEQUE = "cheque", "Cheque"
        WALLET = "wallet", "Wallet"
        INSURANCE = "insurance", "Insurance / TPA settlement"
        ADVANCE = "advance", "Adjusted from deposit"

    bill = models.ForeignKey(Bill, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    transaction_id = models.CharField(max_length=100, blank=True)
    paid_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-paid_at"]

    def __str__(self):
        return f"Payment ₹{self.amount} ({self.payment_method}) for Bill #{self.bill_id}"


class InsuranceClaim(TenantScopedModel):
    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        UNDER_REVIEW = "under_review", "Under Review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SETTLED = "settled", "Settled"

    bill = models.OneToOneField(Bill, on_delete=models.CASCADE, related_name="insurance_claim")
    insurance_company = models.CharField(max_length=150)
    policy_number = models.CharField(max_length=100)
    claimed_amount = models.DecimalField(max_digits=12, decimal_places=2)
    approved_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUBMITTED)

    class Meta:
        ordering = ["-id"]

    def __str__(self):
        return f"InsuranceClaim for Bill #{self.bill_id} ({self.insurance_company}) - {self.status}"
