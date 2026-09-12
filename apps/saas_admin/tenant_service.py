import re
from datetime import timedelta
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.models import Role, User, assign_role
from apps.core.models import ALL_MODULES, Department, Hospital, validate_hospital_slug
from .models import TenantSubscription


@transaction.atomic
def onboard_hospital_tenant(data: dict) -> dict:
    """
    Orchestrates end-to-end onboarding for a new hospital tenant:
    1. Validates slug and checks uniqueness.
    2. Checks uniqueness of owner email.
    3. Creates Hospital entity with enabled modules and address info.
    4. Provisions standard foundational departments (OPD, IPD, Diagnostics, Pharmacy, Billing).
    5. Sets up pre-configured role templates (Owner, OPD Doctor, Front Desk).
    6. Provisions the TenantSubscription tier with billing details.
    7. Creates the initial Hospital Owner/Admin user and links to the Owner role.
    """
    # 1. Validate Hospital Details
    name = (data.get("name") or "").strip()
    if not name:
        raise ValidationError({"name": "Hospital name is required."})

    slug = (data.get("slug") or "").strip().lower()
    if not slug:
        raise ValidationError({"slug": "Subdomain slug is required."})

    try:
        validate_hospital_slug(slug)
    except Exception as exc:
        raise ValidationError({"slug": str(exc)})

    if Hospital.objects.filter(slug=slug).exists():
        raise ValidationError({"slug": f"A hospital tenant with subdomain '{slug}' already exists."})

    city = (data.get("city") or "").strip()
    state = (data.get("state") or "").strip()
    address = (data.get("address") or "").strip()
    primary_language = data.get("primary_language", "en")

    # Modules
    raw_modules = data.get("enabled_modules")
    if raw_modules is None:
        enabled_modules = list(ALL_MODULES)
    else:
        if not isinstance(raw_modules, list):
            raise ValidationError({"enabled_modules": "Enabled modules must be a list of module keys."})
        # Filter only valid modules
        enabled_modules = [m for m in raw_modules if m in ALL_MODULES]

    # 2. Validate Owner Details
    owner_data = data.get("owner") or {}
    owner_email = (owner_data.get("email") or "").strip().lower()
    if not owner_email or "@" not in owner_email:
        raise ValidationError({"owner": {"email": "Valid email address is required for hospital owner."}})

    if User.objects.filter(email=owner_email).exists():
        raise ValidationError({"owner": {"email": f"User with email '{owner_email}' already exists."}})

    owner_first_name = (owner_data.get("first_name") or "Admin").strip()
    owner_last_name = (owner_data.get("last_name") or "User").strip()
    owner_phone = (owner_data.get("phone") or "").strip()
    owner_password = owner_data.get("password") or "Hospital@123"

    # 3. Validate Subscription Details
    sub_data = data.get("subscription") or {}
    tier = sub_data.get("tier", TenantSubscription.Tier.STARTER)
    if tier not in TenantSubscription.Tier.values:
        tier = TenantSubscription.Tier.STARTER

    billing_cycle = sub_data.get("billing_cycle", TenantSubscription.BillingCycle.MONTHLY)
    if billing_cycle not in TenantSubscription.BillingCycle.values:
        billing_cycle = TenantSubscription.BillingCycle.MONTHLY

    try:
        base_price = Decimal(str(sub_data.get("base_price", 0)))
    except Exception:
        base_price = Decimal(0)

    try:
        max_staff_users = int(sub_data.get("max_staff_users", 15))
    except Exception:
        max_staff_users = 15

    # 4. Create Hospital Entity
    hospital = Hospital.objects.create(
        name=name,
        slug=slug,
        city=city,
        state=state,
        address=address,
        primary_language=primary_language,
        enabled_modules=enabled_modules,
        is_active=True,
    )

    # 5. Create Core Departments
    opd_dept, _ = Department.objects.get_or_create(hospital=hospital, name="Outpatient (OPD)", defaults={"code": "OPD"})
    ipd_dept, _ = Department.objects.get_or_create(hospital=hospital, name="Inpatient (IPD)", defaults={"code": "IPD"})
    diag_dept, _ = Department.objects.get_or_create(hospital=hospital, name="Diagnostics & Lab", defaults={"code": "DIAG"})
    pharm_dept, _ = Department.objects.get_or_create(hospital=hospital, name="Pharmacy", defaults={"code": "PHARM"})
    bill_dept, _ = Department.objects.get_or_create(hospital=hospital, name="Billing & Finance", defaults={"code": "BILL"})

    # 6. Create Default Starter Roles with Templates
    owner_role = Role.objects.create(
        hospital=hospital,
        name="Hospital Owner / Admin",
        template=Role.Template.OWNER,
        domain=Role.Domain.BOTH,
        description="Full access to hospital CRM and HMS operations.",
    )
    doctor_role = Role.objects.create(
        hospital=hospital,
        department=opd_dept,
        name="OPD Doctor",
        template=Role.Template.DOCTOR,
        domain=Role.Domain.ERP,
        description="Clinical access to appointments, patient records, and e-Rx.",
    )
    front_desk_role = Role.objects.create(
        hospital=hospital,
        department=opd_dept,
        name="Front Desk Officer",
        template=Role.Template.FRONT_DESK,
        domain=Role.Domain.BOTH,
        description="Patient registration, check-ins, and appointments.",
    )

    # 7. Provision Subscription
    started_at = timezone.now().date()
    days_to_add = 365 if billing_cycle == TenantSubscription.BillingCycle.ANNUAL else 30
    next_billing_date = started_at + timedelta(days=days_to_add)

    subscription = TenantSubscription.objects.create(
        hospital=hospital,
        tier=tier,
        billing_cycle=billing_cycle,
        base_price=base_price,
        max_staff_users=max_staff_users,
        status=TenantSubscription.Status.ACTIVE,
        started_at=started_at,
        next_billing_date=next_billing_date,
    )

    # 8. Create Owner User
    user = User.objects.create(
        email=owner_email,
        first_name=owner_first_name,
        last_name=owner_last_name,
        phone=owner_phone or None,
        hospital=hospital,
        department=opd_dept,
        is_staff=True,
        is_superuser=False,
        is_saas_admin=False,
        preferred_language=primary_language,
    )
    user.set_password(owner_password)
    user.save()
    assign_role(user, owner_role)

    return {
        "hospital": hospital,
        "subscription": subscription,
        "owner": user,
    }
