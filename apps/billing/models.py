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
