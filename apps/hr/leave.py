"""Leave balances: what an employee has of a leave type in a (calendar)
leave year, and the checks made when leave is applied for and approved.

available = opening (carried forward) + accrued + adjustment - approved days.
Pending requests are shown separately and also count when a new request is
checked, so two requests can't both spend the same days.

Accrued: the yearly quota, pro-rated for the months left in the joining
year (if the type says so); for monthly accrual, quota / 12 for each month
started so far. Rounded down to the half day.
"""
import math
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Employee, LeaveBalance, LeaveRequest, LeaveType

ZERO = Decimal("0")


def _half_down(x):
    return Decimal(math.floor(Decimal(x) * 2)) / 2


def accrued(employee, leave_type, year, as_of=None):
    as_of = as_of or timezone.localdate()
    joined = employee.date_of_joining
    if joined and joined.year > year:
        return ZERO
    first_month = joined.month if (joined and joined.year == year and leave_type.prorate_on_joining) else 1
    if leave_type.accrual == LeaveType.Accrual.MONTHLY:
        last_month = 12 if as_of.year > year else (as_of.month if as_of.year == year else 0)
        months = max(last_month - first_month + 1, 0)
    else:
        months = 0 if as_of.year < year else 13 - first_month
    return _half_down(leave_type.annual_quota * months / 12)


def _days(qs):
    return qs.aggregate(n=Sum("days"))["n"] or ZERO


def balance(employee, leave_type, year, as_of=None, exclude=None):
    stored = LeaveBalance.objects.filter(employee=employee, leave_type=leave_type, year=year).first()
    reqs = LeaveRequest.objects.filter(employee=employee, leave_type_master=leave_type, start_date__year=year)
    if exclude is not None:
        reqs = reqs.exclude(pk=exclude.pk)
    opening = stored.opening if stored else ZERO
    adjustment = stored.adjustment if stored else ZERO
    acc = accrued(employee, leave_type, year, as_of)
    used = _days(reqs.filter(status=LeaveRequest.Status.APPROVED))
    pending = _days(reqs.filter(status=LeaveRequest.Status.PENDING))
    return {
        "leave_type": leave_type.pk, "leave_type_name": leave_type.name, "code": leave_type.code, "year": year,
        "opening": opening, "accrued": acc, "adjustment": adjustment, "used": used, "pending": pending,
        "available": opening + acc + adjustment - used, "when_short": leave_type.when_short, "is_paid": leave_type.is_paid,
    }


def summary(employee, year, as_of=None):
    types = LeaveType.objects.filter(hospital_id=employee.hospital_id, is_active=True)
    return [balance(employee, t, year, as_of) for t in types]


def validate_request(req):
    """On apply: dates, then the balance (block, or flag for the approver)."""
    if req.end_date < req.start_date:
        raise ValidationError({"end_date": "Ends before it starts."})
    if req.half_day and req.start_date != req.end_date:
        raise ValidationError({"half_day": "A half day is a single date."})
    lt = req.leave_type_master
    if lt is None:
        return req
    if not lt.is_active:
        raise ValidationError({"leave_type_master": "That leave type is inactive."})
    if req.start_date.year != req.end_date.year:
        raise ValidationError({"end_date": f"Apply for {req.start_date.year} and {req.end_date.year} separately — balances are per year."})
    days = Decimal("0.5") if req.half_day else Decimal((req.end_date - req.start_date).days + 1)
    b = balance(req.employee, lt, req.start_date.year, exclude=req if req.pk else None)
    left = b["available"] - b["pending"]
    req.short_balance = days > left
    if req.short_balance and lt.when_short == LeaveType.WhenShort.BLOCK:
        raise ValidationError({"detail": f"Not enough {lt.name}: {left} day(s) left (after pending requests), {days} asked."})
    return req


def validate_approval(req):
    lt = req.leave_type_master
    if lt is None:
        return
    b = balance(req.employee, lt, req.start_date.year, exclude=req)
    if req.days > b["available"] and lt.when_short == LeaveType.WhenShort.BLOCK:
        raise ValidationError({"detail": f"Not enough {lt.name}: {b['available']} day(s) available, {req.days} asked."})


def unpaid_days(employee, start, end):
    """Approved unpaid leave days falling in [start, end] (for payroll)."""
    total = ZERO
    reqs = LeaveRequest.objects.filter(employee=employee, status=LeaveRequest.Status.APPROVED, start_date__lte=end, end_date__gte=start).select_related("leave_type_master")
    for r in reqs:
        unpaid = (not r.leave_type_master.is_paid) if r.leave_type_master_id else "unpaid" in r.leave_type.lower()
        if unpaid:
            total += Decimal("0.5") if r.half_day else Decimal((min(r.end_date, end) - max(r.start_date, start)).days + 1)
    return total


@transaction.atomic
def carry_forward(hospital_id, year):
    """Year end: each employee's unused days of every carry-forward type,
    capped, become next year's opening balance. Types that don't carry
    forward lapse (opening 0). Safe to re-run."""
    year_end = date(year, 12, 31)
    types = list(LeaveType.objects.filter(hospital_id=hospital_id, is_active=True))
    done = 0
    for emp in Employee.objects.filter(hospital_id=hospital_id):
        for lt in types:
            carry = ZERO
            if lt.carry_forward_max > 0:
                carry = min(max(balance(emp, lt, year, as_of=year_end)["available"], ZERO), lt.carry_forward_max)
            if carry == 0 and not LeaveBalance.objects.filter(employee=emp, leave_type=lt, year=year + 1).exists():
                continue
            LeaveBalance.objects.update_or_create(
                employee=emp, leave_type=lt, year=year + 1,
                defaults={"hospital_id": hospital_id, "opening": carry, "carried_forward_at": timezone.now()},
            )
            done += 1
    return done
