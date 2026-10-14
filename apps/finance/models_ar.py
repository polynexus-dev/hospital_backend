"""Customer (B2B) invoicing: corporate clients, insurers billed directly,
camps, rent/canteen/space licensees... Tax invoices with GST, receipts,
the customer ledger, ageing, and recurring invoices. Imported by
finance.models."""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel

ZERO = Decimal("0")


class AccountsSettings(TenantScopedModel):
    """The hospital as a supplier (printed on tax invoices) and the
    maker-checker rule for manual vouchers."""

    legal_name = models.CharField(max_length=200, blank=True, help_text="Blank = the hospital's name")
    gstin = models.CharField(max_length=15, blank=True)
    pan = models.CharField(max_length=10, blank=True)
    state_code = models.CharField(max_length=2, blank=True, help_text="GST state code, e.g. 27 = Maharashtra")
    invoice_prefix = models.CharField(max_length=10, default="INV")
    bank_details = models.TextField(blank=True, help_text="Printed on invoices: bank, account no., IFSC, UPI")
    invoice_terms = models.TextField(blank=True)
    voucher_approval_required = models.BooleanField(default=True, help_text="Manual vouchers post only after a second person approves them")

    class Meta:
        app_label = "finance"
        constraints = [models.UniqueConstraint(fields=["hospital"], name="one_accounts_settings_per_hospital")]

    @classmethod
    def for_hospital(cls, hospital_id):
        obj, _ = cls.objects.get_or_create(hospital_id=hospital_id)
        return obj


class Customer(TenantScopedModel):
    class Kind(models.TextChoices):
        CORPORATE = "corporate", "Corporate"
        INSURER = "insurer", "Insurer / TPA"
        GOVERNMENT = "government", "Government scheme"
        OTHER = "other", "Other"

    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.CORPORATE)
    gstin = models.CharField(max_length=15, blank=True)
    pan = models.CharField(max_length=10, blank=True)
    state_code = models.CharField(max_length=2, blank=True, help_text="Place of supply; blank = same state as the hospital")
    billing_address = models.TextField(blank=True)
    contact_person = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    credit_days = models.PositiveSmallIntegerField(default=30)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "finance"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["hospital", "name"], name="unique_customer_name")]

    def __str__(self):
        return self.name


class RecurringInvoice(TenantScopedModel):
    """A template that raises an invoice every period (monthly rent, AMC,
    corporate health-check retainer...)."""

    class Frequency(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        QUARTERLY = "quarterly", "Quarterly"
        HALF_YEARLY = "half_yearly", "Half-yearly"
        YEARLY = "yearly", "Yearly"

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="recurring_invoices")
    name = models.CharField(max_length=150)
    frequency = models.CharField(max_length=12, choices=Frequency.choices, default=Frequency.MONTHLY)
    lines = models.JSONField(default=list, help_text="[{tariff?, description, hsn_sac, quantity, rate, discount, gst_rate}]")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    next_run_date = models.DateField()
    auto_issue = models.BooleanField(default=True, help_text="Issue (post) the invoice at once; otherwise leave it as a draft to review")
    auto_email = models.BooleanField(default=False, help_text="Email the issued invoice to the customer")
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        app_label = "finance"
        ordering = ["next_run_date"]

    def __str__(self):
        return self.name


class CustomerInvoice(TenantScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        PARTIALLY_PAID = "partially_paid", "Partially paid"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="invoices")
    invoice_number = models.CharField(max_length=40, blank=True)
    invoice_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    is_interstate = models.BooleanField(default=False)
    taxable_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cgst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    sgst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    igst = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    amount_received = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text="Receipts + TDS allocated to this invoice")
    notes = models.TextField(blank=True)
    recurring = models.ForeignKey(RecurringInvoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices")
    recurring_period = models.DateField(null=True, blank=True, help_text="The run date this recurring invoice was raised for")
    issued_at = models.DateTimeField(null=True, blank=True)
    cancelled_reason = models.CharField(max_length=255, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    emailed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        app_label = "finance"
        ordering = ["-invoice_date", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["hospital", "invoice_number"], condition=~models.Q(invoice_number=""), name="unique_customer_invoice_number"),
            models.UniqueConstraint(fields=["recurring", "recurring_period"], name="one_invoice_per_recurring_period"),
        ]

    @property
    def outstanding(self):
        if self.status in (self.Status.DRAFT, self.Status.CANCELLED):
            return ZERO
        return max(self.total_amount - self.amount_received, ZERO)


class CustomerInvoiceLine(models.Model):
    invoice = models.ForeignKey(CustomerInvoice, on_delete=models.CASCADE, related_name="lines")
    tariff = models.ForeignKey("finance.ServiceTariff", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    description = models.CharField(max_length=255)
    hsn_sac = models.CharField(max_length=10, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    rate = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    taxable_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        app_label = "finance"
        ordering = ["id"]


class CustomerReceipt(TenantScopedModel):
    class Mode(models.TextChoices):
        NEFT = "neft", "NEFT / RTGS"
        UPI = "upi", "UPI"
        CHEQUE = "cheque", "Cheque"
        CASH = "cash", "Cash"

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="receipts")
    invoice = models.ForeignKey(CustomerInvoice, on_delete=models.PROTECT, null=True, blank=True, related_name="receipts",
                                help_text="Blank = on account (advance)")
    received_on = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    tds_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text="TDS the customer deducted")
    mode = models.CharField(max_length=8, choices=Mode.choices, default=Mode.NEFT)
    reference = models.CharField(max_length=60, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        app_label = "finance"
        ordering = ["-received_on", "-id"]
