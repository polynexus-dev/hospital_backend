from django.conf import settings
from django.db import models

from apps.core.managers import TenantManager
from apps.core.models import Hospital, TimeStampedModel


class TenantSubscription(TimeStampedModel):
    """One active plan per hospital tenant — SaaS-side billing metadata,
    not a TenantScopedModel: it's managed exclusively by SaaS admins
    (apps.core.permissions.IsSaaSAdmin), never by the hospital's own
    users, and a hospital's own TenantScopedViewSetMixin-based views have
    no reason to ever list every hospital's subscriptions the way that
    mixin's tenant-scoping would otherwise imply."""

    class Tier(models.TextChoices):
        STARTER = "starter", "Starter"
        PRO = "pro", "Pro"
        ENTERPRISE = "enterprise", "Enterprise"

    class BillingCycle(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        ANNUAL = "annual", "Annual"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        CANCELLED = "cancelled", "Cancelled"

    hospital = models.OneToOneField(Hospital, on_delete=models.CASCADE, related_name="subscription")
    tier = models.CharField(max_length=16, choices=Tier.choices, default=Tier.STARTER)
    billing_cycle = models.CharField(max_length=16, choices=BillingCycle.choices, default=BillingCycle.MONTHLY)
    base_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    max_staff_users = models.PositiveIntegerField(
        default=10,
        help_text="Enforced on user creation — see apps.accounts.views.UserViewSet.perform_create. 0 means unlimited.",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    started_at = models.DateField()
    next_billing_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.hospital.name} — {self.get_tier_display()} ({self.get_status_display()})"


class InvoiceSequence(models.Model):
    """Backs the atomic, gapless invoice_number generation in
    apps.saas_admin.services.generate_invoice_number() — one row per
    Indian financial year (Apr-Mar), incremented under
    select_for_update() the same way apps.patients.Patient._generate_uhid
    locks Hospital.next_uhid_sequence. Not a TenantScopedModel/
    TimeStampedModel: this is the platform's own numbering ledger
    (INV-2025-26-00001, ...), shared across every hospital's invoices,
    not owned by any one tenant."""

    financial_year = models.CharField(max_length=9, unique=True, help_text='e.g. "2025-26"')
    next_number = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"FY {self.financial_year} — next {self.next_number}"


class TenantInvoice(TimeStampedModel):
    """A single SaaS billing-period invoice for one hospital. Not a
    TenantScopedModel for the same reason as TenantSubscription above —
    this is what the platform bills the hospital, not something the
    hospital's own users create or list for themselves."""

    class Status(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PAID = "paid", "Paid"
        OVERDUE = "overdue", "Overdue"

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="invoices")
    subscription = models.ForeignKey(TenantSubscription, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices")
    # Server-generated only — see apps.saas_admin.services.generate_invoice_number
    # and TenantInvoiceViewSet.perform_create. Globally unique (not just
    # per-hospital) because the FY sequence itself is global, matching
    # how a real invoice ledger numbers consecutively across every
    # customer, not per-customer.
    invoice_number = models.CharField(max_length=32, unique=True, editable=False)
    billing_period_start = models.DateField()
    billing_period_end = models.DateField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UNPAID)
    due_date = models.DateField()
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_receipt = models.FileField(upload_to="tenant_invoice_receipts/%Y/%m/", null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-billing_period_start"]
        indexes = [models.Index(fields=["hospital", "status"])]

    def __str__(self):
        return f"Invoice {self.invoice_number} — {self.hospital.name} ({self.get_status_display()})"


class TenantUsageSnapshot(TimeStampedModel):
    """One row per hospital per billing month — computed by
    apps.saas_admin.tasks.compute_monthly_tenant_usage (Celery beat,
    monthly) rather than derived live on every dashboard load, since the
    storage-consumption figure walks every Document row for the hospital
    (see apps.saas_admin.services.compute_tenant_usage) and isn't cheap
    enough to recompute per request."""

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="usage_snapshots")
    period_start = models.DateField()
    period_end = models.DateField()
    active_staff_count = models.PositiveIntegerField(default=0)
    patients_registered_count = models.PositiveIntegerField(default=0)
    bills_generated_count = models.PositiveIntegerField(default=0)
    storage_bytes_used = models.BigIntegerField(
        default=0,
        help_text="Approximation — sums apps.patients.Document.file sizes only, not every file-bearing model on the platform.",
    )

    class Meta:
        ordering = ["-period_start"]
        constraints = [
            models.UniqueConstraint(fields=["hospital", "period_start"], name="unique_tenantusagesnapshot_per_hospital_period"),
        ]

    def __str__(self):
        return f"Usage for {self.hospital.name} ({self.period_start:%Y-%m})"


class SupportTicket(TimeStampedModel):
    """Raised by a hospital's own staff (TenantScopedViewSetMixin-based
    creation via the hospital-side ViewSet), resolved by SaaS admins
    across every hospital (the separate SaaS-side ViewSet) — see
    apps.saas_admin.views for why this is two ViewSets over one model
    rather than one with conditional behavior. `objects = TenantManager()`
    here (unlike TenantSubscription/TenantInvoice above) because the
    hospital-side create/list path genuinely wants the same tenant-scoped
    manager convention every other hospital-facing model uses."""

    class Category(models.TextChoices):
        BUG = "bug", "Bug report"
        FEATURE_REQUEST = "feature_request", "Feature request"
        BILLING = "billing", "Billing"
        GENERAL = "general", "General"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In Progress"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="support_tickets")
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    subject = models.CharField(max_length=255)
    description = models.TextField()
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.GENERAL)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    resolution_notes = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    objects = TenantManager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["hospital", "status"])]

    def __str__(self):
        return f"[{self.get_status_display()}] {self.subject} ({self.hospital.name})"
