from django.conf import settings
from django.db import models
from django.utils import timezone
from apps.core.models import TenantScopedModel


class Ledger(TenantScopedModel):
    class EntryType(models.TextChoices):
        REVENUE = "revenue", "Revenue"
        EXPENSE = "expense", "Expense"

    entry_type = models.CharField(max_length=20, choices=EntryType.choices)
    category = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference_type = models.CharField(max_length=100, blank=True)
    reference_id = models.CharField(max_length=100, blank=True)
    entry_date = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-entry_date"]

    def __str__(self):
        return f"Ledger [{self.entry_type}]: {self.category} - {self.amount}"


class Expense(TenantScopedModel):
    category = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_to = models.CharField(max_length=255)
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="paid_expenses")
    expense_date = models.DateField(default=timezone.localdate)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_expenses",
    )

    class Meta:
        ordering = ["-expense_date"]

    def __str__(self):
        return f"Expense ({self.category}): {self.amount} to {self.paid_to}"


class Receivable(TenantScopedModel):
    class SourceType(models.TextChoices):
        INSURANCE_CLAIM = "insurance_claim", "Insurance Claim"
        CORPORATE_BILLING = "corporate_billing", "Corporate Billing"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RECEIVED = "received", "Received"
        WRITTEN_OFF = "written_off", "Written Off"

    source_type = models.CharField(max_length=30, choices=SourceType.choices)
    source_id = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    due_date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    class Meta:
        ordering = ["due_date"]

    def __str__(self):
        return f"Receivable ({self.source_type} #{self.source_id}): {self.amount} ({self.status})"
