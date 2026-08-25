"""Business logic for the platform-management surface — kept out of
views.py the same way apps.analytics.services is (a view's job is
request/response shape, not the query itself), and unit-testable without
going through DRF."""

from django.db.models import Sum

from apps.core.models import ALL_MODULE_KEYS, Hospital


def platform_analytics_snapshot():
    """Platform-wide KPIs for the SaaS admin dashboard. `Bill`/`Patient`
    queries use `.unscoped()` explicitly rather than the bare default
    TenantManager — the requesting SaaS admin's own session may itself
    carry a resolved tenant (subdomain/X-Tenant fallback in
    TenantMiddleware), and this must never silently narrow to that one
    hospital's numbers. `Hospital` itself isn't a TenantScopedModel (it
    IS the tenant), so its own manager is already unscoped by definition."""
    from apps.billing.models import Bill
    from apps.patients.models import Patient

    hospitals = Hospital.objects.all()
    total_hospitals = hospitals.count()
    active_hospitals = hospitals.filter(is_active=True).count()
    total_revenue = Bill.objects.unscoped().aggregate(total=Sum("net_amount"))["total"] or 0
    total_patients = Patient.objects.unscoped().count()

    # Python-side rather than a JSONField __contains query — enabled_modules
    # is a small JSON array per hospital, active_hospitals is never a large
    # number, and __contains portability across the Postgres/SQLite split
    # this project runs (base.py's USE_SQLITE toggle) isn't worth the risk
    # for what's an occasional dashboard call, not a hot path.
    adoption_counts = dict.fromkeys(ALL_MODULE_KEYS, 0)
    for enabled_modules in hospitals.filter(is_active=True).values_list("enabled_modules", flat=True):
        for module_key in enabled_modules or []:
            if module_key in adoption_counts:
                adoption_counts[module_key] += 1
    module_adoption = {
        module_key: round(count / active_hospitals * 100, 1) if active_hospitals else 0.0
        for module_key, count in adoption_counts.items()
    }

    return {
        "total_hospitals": total_hospitals,
        "active_hospitals": active_hospitals,
        "total_revenue": float(total_revenue),
        "total_patients": total_patients,
        "module_adoption_percent": module_adoption,
    }


def compute_tenant_usage(hospital, period_start, period_end):
    """The per-hospital metrics behind one TenantUsageSnapshot row.
    `period_start`/`period_end` bound patient-registration and
    bill-generation counts (activity *during* the period); active staff
    and storage are point-in-time as-of-now figures, not period-bounded —
    "how many staff/how much storage right now" is what a capacity/billing
    conversation actually needs, not "how many as of the last day of last
    month"."""
    from apps.accounts.models import User
    from apps.billing.models import Bill
    from apps.patients.models import Document, Patient

    active_staff_count = User.objects.filter(hospital=hospital, is_active=True).count()
    patients_registered_count = Patient.objects.filter(
        hospital=hospital, created_at__gte=period_start, created_at__lt=period_end,
    ).count()
    bills_generated_count = Bill.objects.filter(
        hospital=hospital, created_at__gte=period_start, created_at__lt=period_end,
    ).count()

    storage_bytes_used = 0
    for document in Document.objects.filter(hospital=hospital).only("file"):
        try:
            storage_bytes_used += document.file.size
        except (FileNotFoundError, OSError, ValueError):
            continue  # missing/unreadable file on disk — don't fail the whole snapshot over it

    return {
        "active_staff_count": active_staff_count,
        "patients_registered_count": patients_registered_count,
        "bills_generated_count": bills_generated_count,
        "storage_bytes_used": storage_bytes_used,
    }
