"""Leave rules (MedNet M2.22 parity): leave types with a yearly quota,
accrual and carry-forward cap, per-employee yearly balances; staff
dependents linked to patient records (staff rate in billing); overtime
paid through payroll. Imported by hr.models."""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel


class LeaveType(TenantScopedModel):
    """A leave type and its rules. The leave year is the calendar year."""

    class Accrual(models.TextChoices):
        YEARLY = "yearly", "Full quota on 1 January"
        MONTHLY = "monthly", "Quota / 12 credited each month"

    class WhenShort(models.TextChoices):
        BLOCK = "block", "Block — no negative balance"
        WARN = "warn", "Warn — allow, flagged for the approver"

    code = models.CharField(max_length=10)
    name = models.CharField(max_length=60)
    annual_quota = models.DecimalField(max_digits=5, decimal_places=1, default=0, help_text="Days per leave year.")
    accrual = models.CharField(max_length=8, choices=Accrual.choices, default=Accrual.YEARLY)
    prorate_on_joining = models.BooleanField(default=True, help_text="Joiners get the quota for the months left in their joining year.")
    carry_forward_max = models.DecimalField(max_digits=5, decimal_places=1, default=0, help_text="Unused days carried into next year, at most this many; 0 = lapses.")
    when_short = models.CharField(max_length=6, choices=WhenShort.choices, default=WhenShort.BLOCK)
    is_paid = models.BooleanField(default=True, help_text="Unpaid leave days are cut from payroll paid days.")
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "hr"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["hospital", "code"], name="unique_leave_type_code")]

    def __str__(self):
        return self.name


class LeaveBalance(TenantScopedModel):
    """The stored part of an employee's balance for one type and year: the
    days carried forward into it and any manual adjustment. Accrued, used and
    pending days are worked out from the type's rules and the requests
    (apps.hr.leave.balance)."""

    employee = models.ForeignKey("hr.Employee", on_delete=models.CASCADE, related_name="leave_balances")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE, related_name="balances")
    year = models.PositiveSmallIntegerField()
    opening = models.DecimalField(max_digits=5, decimal_places=1, default=0, help_text="Carried forward from last year.")
    adjustment = models.DecimalField(max_digits=5, decimal_places=1, default=0, help_text="Manual credit (+) or debit (-).")
    note = models.CharField(max_length=255, blank=True)
    carried_forward_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        app_label = "hr"
        ordering = ["-year", "employee__employee_code", "leave_type__name"]
        constraints = [models.UniqueConstraint(fields=["employee", "leave_type", "year"], name="one_leave_balance_per_type_year")]


class EmployeeDependent(TenantScopedModel):
    """A staff member's family member, linked to their patient record so
    their bills get the staff rate category."""

    class Relationship(models.TextChoices):
        SPOUSE = "spouse", "Spouse"
        CHILD = "child", "Child"
        PARENT = "parent", "Parent"
        PARENT_IN_LAW = "parent_in_law", "Parent-in-law"
        SIBLING = "sibling", "Sibling"
        OTHER = "other", "Other"

    employee = models.ForeignKey("hr.Employee", on_delete=models.CASCADE, related_name="dependents")
    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="staff_dependencies")
    relationship = models.CharField(max_length=16, choices=Relationship.choices)
    valid_until = models.DateField(null=True, blank=True, help_text="e.g. a child's 25th birthday; blank = no end.")
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "hr"
        ordering = ["employee__employee_code", "relationship"]
        constraints = [models.UniqueConstraint(fields=["employee", "patient"], name="unique_dependent_per_employee")]


class OvertimeEntry(TenantScopedModel):
    """Overtime hours worked; approved hours are paid in that month's
    payroll and count towards the ESI wage."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    employee = models.ForeignKey("hr.Employee", on_delete=models.CASCADE, related_name="overtime_entries")
    date = models.DateField(default=timezone.localdate)
    hours = models.DecimalField(max_digits=4, decimal_places=1)
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "hr"
        ordering = ["-date"]
