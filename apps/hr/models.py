from django.conf import settings
from django.db import models
from django.utils import timezone
from apps.core.models import TenantScopedModel
from apps.facilities.models import Department


class Employee(TenantScopedModel):
    class EmploymentType(models.TextChoices):
        PERMANENT = "permanent", "Permanent"
        CONTRACT = "contract", "Contract"
        VISITING = "visiting", "Visiting"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_profile",
    )
    employee_code = models.CharField(max_length=50)
    # Printed before the name on the payslip and staff documents (Mr. / Ms. / Dr. …).
    name_prefix = models.CharField(max_length=10, blank=True, help_text="e.g. Mr., Ms., Mrs., Dr.")
    marital_status = models.CharField(max_length=10, blank=True, choices=[("single", "Single"), ("married", "Married"), ("widowed", "Widowed"), ("divorced", "Divorced")],
                                      help_text="Used by the dependent policy (how many dependents may be linked).")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="employees")
    designation = models.CharField(max_length=100)
    date_of_joining = models.DateField(default=timezone.localdate)
    employment_type = models.CharField(max_length=20, choices=EmploymentType.choices, default=EmploymentType.PERMANENT)
    bank_account_number = models.CharField(max_length=100, blank=True)
    pan_number = models.CharField(max_length=50, blank=True)
    # The employee's own patient record — staff rate category in billing
    # (dependents: hr.EmployeeDependent).
    patient = models.OneToOneField("patients.Patient", on_delete=models.SET_NULL, null=True, blank=True, related_name="employee_record")

    class Meta:
        unique_together = ("hospital", "employee_code")
        ordering = ["employee_code"]

    def __str__(self):
        user_str = f" ({self.user.get_full_name()})" if self.user else ""
        return f"Employee [{self.employee_code}] - {self.designation}{user_str}"


class Attendance(TenantScopedModel):
    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"
        HALF_DAY = "half_day", "Half Day"
        LEAVE = "leave", "On Leave"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="attendance_records")
    date = models.DateField(default=timezone.localdate)
    check_in = models.DateTimeField(null=True, blank=True)
    check_out = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PRESENT)

    class Meta:
        unique_together = ("employee", "date")
        ordering = ["-date"]

    def __str__(self):
        return f"Attendance {self.date} for {self.employee}: {self.status}"


class LeaveRequest(TenantScopedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="leave_requests")
    # Label shown on screens; the leave type's name when `leave_type_master` is set.
    leave_type = models.CharField(max_length=50, blank=True)
    leave_type_master = models.ForeignKey("hr.LeaveType", on_delete=models.PROTECT, null=True, blank=True, related_name="requests")
    start_date = models.DateField()
    end_date = models.DateField()
    half_day = models.BooleanField(default=False, help_text="Single-day request for half a day.")
    days = models.DecimalField(max_digits=5, decimal_places=1, default=0, editable=False)
    reason = models.CharField(max_length=255, blank=True)
    # Applied for more than the balance on a "warn" leave type.
    short_balance = models.BooleanField(default=False, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_leaves",
    )

    class Meta:
        ordering = ["-start_date"]

    def save(self, *args, **kwargs):
        from decimal import Decimal

        self.days = Decimal("0.5") if self.half_day else Decimal((self.end_date - self.start_date).days + 1)
        if self.leave_type_master_id and not self.leave_type:
            self.leave_type = self.leave_type_master.name[:50]
        super().save(*args, **kwargs)

    def __str__(self):
        return f"LeaveRequest ({self.leave_type}) for {self.employee}: {self.status}"


class Shift(TenantScopedModel):
    class ShiftType(models.TextChoices):
        MORNING = "morning", "Morning Shift"
        EVENING = "evening", "Evening Shift"
        NIGHT = "night", "Night Shift"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="shifts")
    shift_date = models.DateField()
    shift_type = models.CharField(max_length=20, choices=ShiftType.choices, default=ShiftType.MORNING)

    class Meta:
        unique_together = ("employee", "shift_date")
        ordering = ["-shift_date"]

    def __str__(self):
        return f"Shift ({self.shift_type}) on {self.shift_date} for {self.employee}"


from .models_hrm import (  # noqa: E402,F401
    Appraisal,
    Candidate,
    DutyRule,
    ExitRequest,
    JobOpening,
    PayrollRun,
    Payslip,
    RosterPublication,
    SalaryStructure,
    StaffProfile,
    TrainingAttendance,
    TrainingProgram,
)
from .models_leave import DependentAllowance, DependentRelationRule, EmployeeDependent, LeaveBalance, LeaveType, OvertimeEntry  # noqa: E402,F401
