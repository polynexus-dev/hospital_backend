from datetime import date

from celery import shared_task

from apps.core.models import Hospital

from .models import TenantUsageSnapshot
from .services import compute_tenant_usage


def _previous_month_bounds(today):
    period_end = today.replace(day=1)
    previous_month_end = period_end
    if period_end.month == 1:
        period_start = period_end.replace(year=period_end.year - 1, month=12)
    else:
        period_start = period_end.replace(month=period_end.month - 1)
    return period_start, previous_month_end


@shared_task
def compute_monthly_tenant_usage():
    """Runs once a month on Celery beat (early on the 1st) — computes last
    month's per-hospital usage snapshot for the SaaS admin usage/billing
    dashboard. Skips suspended hospitals — same reasoning as every other
    per-hospital-loop Celery task in this codebase (see
    apps.appointments.tasks.sweep_patient_recalls)."""
    period_start, period_end = _previous_month_bounds(date.today())

    created = 0
    for hospital in Hospital.objects.filter(is_active=True):
        metrics = compute_tenant_usage(hospital, period_start, period_end)
        TenantUsageSnapshot.objects.update_or_create(
            hospital=hospital, period_start=period_start,
            defaults={"period_end": period_end, **metrics},
        )
        created += 1
    return created
