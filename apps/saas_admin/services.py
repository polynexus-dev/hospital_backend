"""Business logic for the platform-management surface — kept out of
views.py the same way apps.analytics.services is (a view's job is
request/response shape, not the query itself), and unit-testable without
going through DRF."""

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.models import ALL_MODULE_KEYS, Hospital


def current_financial_year(today=None):
    """Indian financial year: 1 Apr - 31 Mar, written "2025-26" for the
    year starting April 2025. `today` is injectable for tests; defaults
    to the server's local date (TIME_ZONE=Asia/Kolkata — see
    config/settings/base.py)."""
    today = today or timezone.localdate()
    start_year = today.year if today.month >= 4 else today.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def generate_invoice_number(today=None):
    """Atomically issues the next INV-<FY>-NNNNN number, gapless and
    globally unique — same race-safety pattern as
    apps.patients.Patient._generate_uhid (select_for_update() on a
    per-key counter row, incremented inside one transaction). Two-step
    rather than one `select_for_update().get_or_create()` call because
    the *first* invoice of a new financial year has no row to lock yet —
    get_or_create() already handles that creation race internally
    (it catches the IntegrityError a concurrent first-caller would hit
    and re-fetches), then the select_for_update().get() right after
    takes the lock for the actual increment it needs."""
    from .models import InvoiceSequence

    fy = current_financial_year(today)
    with transaction.atomic():
        InvoiceSequence.objects.get_or_create(financial_year=fy)
        sequence = InvoiceSequence.objects.select_for_update().get(financial_year=fy)
        number = sequence.next_number
        sequence.next_number = number + 1
        sequence.save(update_fields=["next_number"])
    return f"INV-{fy}-{number:05d}"


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
