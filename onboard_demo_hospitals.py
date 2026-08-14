import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

from django.utils import timezone
from apps.core.models import Hospital, ALL_MODULE_KEYS
from apps.patients.models import Patient
from apps.billing.models import Bill
from apps.accounts.models import User, Role
from apps.accounts.permission_templates import apply_permission_template

now = timezone.now()

new_hospitals = [
    {
        "name": "Apollo Superspecialty Hospital",
        "slug": "apollo-mumbai",
        "city": "Mumbai",
        "state": "Maharashtra",
        "revenue": 145000.00,
        "modules": ALL_MODULE_KEYS,
        "patients": [
            {"first_name": "Rohan", "last_name": "Mehta", "mobile": "+919892011111", "city": "Mumbai"},
            {"first_name": "Pooja", "last_name": "Deshmukh", "mobile": "+919892022222", "city": "Mumbai"},
            {"first_name": "Siddharth", "last_name": "Kapoor", "mobile": "+919892033333", "city": "Mumbai"},
        ]
    },
    {
        "name": "Ruby Hall Clinic (Sasoon Branch)",
        "slug": "ruby-hall-pune",
        "city": "Pune",
        "state": "Maharashtra",
        "revenue": 98000.00,
        "modules": ["crm", "ipd", "diagnostics", "pharmacy", "emergency", "ot", "billing", "inventory"],
        "patients": [
            {"first_name": "Amit", "last_name": "More", "mobile": "+919822044444", "city": "Pune"},
            {"first_name": "Neha", "last_name": "Gaikwad", "mobile": "+919822055555", "city": "Pune"},
        ]
    },
    {
        "name": "Manipal Multispecialty Hospital",
        "slug": "manipal-blr",
        "city": "Bengaluru",
        "state": "Karnataka",
        "revenue": 210000.00,
        "modules": ALL_MODULE_KEYS,
        "patients": [
            {"first_name": "Karthik", "last_name": "Rao", "mobile": "+919845066666", "city": "Bengaluru"},
            {"first_name": "Ananya", "last_name": "Hegde", "mobile": "+919845077777", "city": "Bengaluru"},
        ]
    },
    {
        "name": "Max Healthcare Institute",
        "slug": "max-delhi",
        "city": "New Delhi",
        "state": "Delhi NCR",
        "revenue": 175000.00,
        "modules": ["crm", "opd", "ipd", "diagnostics", "billing", "hr", "finance"],
        "patients": [
            {"first_name": "Vikas", "last_name": "Verma", "mobile": "+919810088888", "city": "Delhi"},
            {"first_name": "Sonia", "last_name": "Malhotra", "mobile": "+919810099999", "city": "Delhi"},
        ]
    }
]

print("Onboarding demo hospital branches...")

for data in new_hospitals:
    hosp, created = Hospital.objects.get_or_create(
        slug=data["slug"],
        defaults={
            "name": data["name"],
            "city": data["city"],
            "state": data["state"],
            "is_active": True,
            "enabled_modules": data["modules"],
        }
    )
    if not created:
        hosp.enabled_modules = data["modules"]
        hosp.save()

    # Create Hospital Admin Role & User
    role, _ = Role.objects.get_or_create(
        hospital=hosp,
        name="Hospital Owner / Admin",
        defaults={"template": Role.Template.HOSPITAL_ADMINISTRATOR},
    )
    apply_permission_template(role.group, Role.Template.HOSPITAL_ADMINISTRATOR)

    admin_email = f"admin@{hosp.slug}.example"
    admin_user, _ = User.objects.get_or_create(
        email=admin_email,
        defaults={
            "first_name": "Hospital",
            "last_name": "Admin",
            "hospital": hosp,
            "role": role,
            "is_staff": True,
        }
    )
    admin_user.set_password("changeme123")
    admin_user.groups.add(role.group)
    admin_user.save()

    # Seed Patients & Revenue
    for p_info in data["patients"]:
        p, _ = Patient.objects.get_or_create(
            hospital=hosp,
            mobile=p_info["mobile"],
            defaults={
                "first_name": p_info["first_name"],
                "last_name": p_info["last_name"],
                "city": p_info["city"],
                "gender": "male"
            }
        )
        Bill.objects.get_or_create(
            hospital=hosp,
            patient=p,
            defaults={
                "total_amount": data["revenue"] / len(data["patients"]),
                "discount_amount": 0.0,
                "net_amount": data["revenue"] / len(data["patients"]),
                "status": Bill.Status.PAID
            }
        )

    print(f" [OK] Onboarded Tenant: {hosp.name} ({hosp.city}) -- Admin: {admin_email}")

print("\nAll 4 Hospital Tenants Onboarded Successfully!")
