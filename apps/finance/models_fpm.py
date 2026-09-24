"""NABH FPM chapter models: vendor payables (FPM.2), double-entry ledger
(Tally export), service tariff (FPM.3.a), insurance policy & payer
remittance reconciliation (FPM.4). Imported by finance.models."""
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel


class Vendor(TenantScopedModel):
    name = models.CharField(max_length=200)
    gstin = models.CharField(max_length=15, blank=True)
    pan = models.CharField(max_length=10, blank=True)
    state_code = models.CharField(max_length=2, blank=True, help_text="GST state code, e.g. 27 = Maharashtra")
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    credit_days = models.PositiveSmallIntegerField(default=30, help_text="FPM.2.e payment terms for this supplier.")
    bank_account = models.CharField(max_length=40, blank=True)
    ifsc = models.CharField(max_length=11, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "finance"
        ordering = ["name"]

    def __str__(self):
        return self.name


class VendorInvoice(TenantScopedModel):
    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        MATCHED = "matched", "3-way matched"
        MISMATCH = "mismatch", "Mismatch (on hold)"
        APPROVED = "approved", "Approved for payment"
        PARTIALLY_PAID = "partially_paid", "Partially paid"
        PAID = "paid", "Paid"
        DISPUTED = "disputed", "Disputed"

    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="invoices")
    purchase_order = models.ForeignKey("inventory.PurchaseOrder", on_delete=models.SET_NULL, null=True, blank=True, related_name="vendor_invoices")
    invoice_number = models.CharField(max_length=60)
    invoice_date = models.DateField(default=timezone.localdate)
    taxable_amount = models.DecimalField(max_digits=12, decimal_places=2)
    gst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, editable=False, default=0)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RECEIVED)
    match_notes = models.TextField(blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        app_label = "finance"
        ordering = ["due_date", "-invoice_date"]
        constraints = [models.UniqueConstraint(fields=["hospital", "vendor", "invoice_number"], name="unique_vendor_invoice")]

    def save(self, *args, **kwargs):
        self.total_amount = (self.taxable_amount or 0) + (self.gst_amount or 0)
        if not self.due_date:
            self.due_date = self.invoice_date + timedelta(days=self.vendor.credit_days)
        super().save(*args, **kwargs)

    @property
    def paid_amount(self):
        return sum((p.amount + p.tds_amount for p in self.payments.all()), 0)

    @property
    def outstanding(self):
        return self.total_amount - self.paid_amount


class VendorPayment(TenantScopedModel):
    class Mode(models.TextChoices):
        NEFT = "neft", "NEFT / RTGS"
        UPI = "upi", "UPI"
        CHEQUE = "cheque", "Cheque"
        CASH = "cash", "Cash"

    invoice = models.ForeignKey(VendorInvoice, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    tds_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    mode = models.CharField(max_length=8, choices=Mode.choices, default=Mode.NEFT)
    reference = models.CharField(max_length=60, blank=True, help_text="UTR / cheque number")
    paid_on = models.DateField(default=timezone.localdate)
    vendor_notified_at = models.DateTimeField(null=True, blank=True)
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        app_label = "finance"
        ordering = ["-paid_on"]


class SupplierNote(TenantScopedModel):
    """FPM.2.d debit / credit notes."""

    class Kind(models.TextChoices):
        DEBIT = "debit", "Debit note (we claim from supplier)"
        CREDIT = "credit", "Credit note (supplier credits us)"

    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="notes")
    invoice = models.ForeignKey(VendorInvoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="notes")
    kind = models.CharField(max_length=6, choices=Kind.choices)
    note_number = models.CharField(max_length=30, editable=False, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    gst_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    reason = models.CharField(max_length=255)
    issued_on = models.DateField(default=timezone.localdate)

    class Meta:
        app_label = "finance"
        ordering = ["-issued_on"]

    def save(self, *args, **kwargs):
        if not self.note_number:
            n = SupplierNote.objects.filter(hospital_id=self.hospital_id, kind=self.kind).count() + 1
            self.note_number = f"{'DN' if self.kind == 'debit' else 'CN'}{timezone.localdate():%y}{n:05d}"
        super().save(*args, **kwargs)


class LedgerAccount(TenantScopedModel):
    class Group(models.TextChoices):
        ASSET = "asset", "Asset"
        LIABILITY = "liability", "Liability"
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"
        EQUITY = "equity", "Equity"

    code = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    group = models.CharField(max_length=10, choices=Group.choices)
    tally_group = models.CharField(max_length=60, blank=True, help_text="Tally parent group, e.g. Sundry Debtors, Sales Accounts.")
    is_system = models.BooleanField(default=False)

    class Meta:
        app_label = "finance"
        ordering = ["code"]
        constraints = [models.UniqueConstraint(fields=["hospital", "code"], name="unique_ledger_account_code")]

    def __str__(self):
        return f"{self.code} {self.name}"


class JournalEntry(TenantScopedModel):
    class VoucherType(models.TextChoices):
        SALES = "sales", "Sales"
        RECEIPT = "receipt", "Receipt"
        PURCHASE = "purchase", "Purchase"
        PAYMENT = "payment", "Payment"
        JOURNAL = "journal", "Journal"
        DEBIT_NOTE = "debit_note", "Debit note"
        CREDIT_NOTE = "credit_note", "Credit note"

    voucher_type = models.CharField(max_length=12, choices=VoucherType.choices)
    voucher_number = models.CharField(max_length=40)
    entry_date = models.DateField(default=timezone.localdate)
    narration = models.CharField(max_length=255, blank=True)
    source_type = models.CharField(max_length=40, blank=True)
    source_id = models.CharField(max_length=40, blank=True)
    party_name = models.CharField(max_length=200, blank=True)
    exported_to_tally_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "finance"
        ordering = ["-entry_date", "-id"]
        constraints = [models.UniqueConstraint(fields=["hospital", "source_type", "source_id", "voucher_type"], name="one_voucher_per_source")]


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name="lines")
    debit = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        app_label = "finance"


class ServiceTariff(TenantScopedModel):
    """FPM.3.a rate master. `category_rates` overrides `rate` per patient
    category, e.g. {"private": 1500, "insurance": 1800}."""

    code = models.CharField(max_length=30)
    name = models.CharField(max_length=200)
    department = models.CharField(max_length=60, blank=True)
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    category_rates = models.JSONField(default=dict, blank=True)
    hsn_sac = models.CharField(max_length=10, blank=True, help_text="SAC 9993 for healthcare services; HSN for goods.")
    gst_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "finance"
        ordering = ["department", "name"]
        constraints = [models.UniqueConstraint(fields=["hospital", "code"], name="unique_tariff_code")]

    def rate_for(self, category):
        return self.category_rates.get(category, self.rate)


class InsurancePolicy(TenantScopedModel):
    """FPM.4.a insurance details, eligibility & coverage."""

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="insurance_policies")
    tpa_company = models.ForeignKey("tpa.TPACompany", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    insurer = models.CharField(max_length=150)
    scheme = models.CharField(max_length=100, blank=True, help_text="e.g. PM-JAY, CGHS, corporate group policy")
    policy_number = models.CharField(max_length=80)
    member_id = models.CharField(max_length=80, blank=True)
    sum_insured = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    balance_available = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    valid_from = models.DateField(null=True, blank=True)
    valid_to = models.DateField(null=True, blank=True)
    coverage_notes = models.TextField(blank=True, help_text="Room-rent cap, co-pay, exclusions")
    eligibility_status = models.CharField(max_length=12, default="unverified", help_text="unverified | eligible | ineligible")
    eligibility_checked_at = models.DateTimeField(null=True, blank=True)
    card_image = models.FileField(upload_to="insurance_cards/", blank=True)

    class Meta:
        app_label = "finance"
        ordering = ["-valid_to"]


class ClaimSettlement(TenantScopedModel):
    """FPM.4.g payer remittance & reconciliation."""

    claim = models.ForeignKey("tpa.Claim", on_delete=models.CASCADE, related_name="settlements")
    utr_number = models.CharField(max_length=40)
    amount_received = models.DecimalField(max_digits=12, decimal_places=2)
    tds_deducted = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    disallowed_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    disallowance_reasons = models.TextField(blank=True)
    received_on = models.DateField(default=timezone.localdate)
    is_reconciled = models.BooleanField(default=False, editable=False)
    response_to_payer = models.TextField(blank=True, help_text="Query reply / dispute of disallowances.")

    class Meta:
        app_label = "finance"
        ordering = ["-received_on"]

    def save(self, *args, **kwargs):
        billed = self.claim.billed_amount
        self.is_reconciled = abs((self.amount_received + self.tds_deducted + self.disallowed_amount) - billed) < 1
        super().save(*args, **kwargs)
