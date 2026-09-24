from django.db import migrations

CORE_HMS_MODULES = [
    "opd", "ipd", "nursing", "laboratory", "radiology", "pharmacy",
    "emergency", "ot", "icu", "bloodbank", "finance", "hr", "billing", "inventory",
]
EXTENDED_HMS_MODULES = [
    "telemedicine", "queue", "portal", "infection_control", "quality", "mrd", "dietary",
    "oncology", "cathlab", "schemes", "support_services", "predictive", "abdm",
]


def enable_for_hms_hospitals(apps, schema_editor):
    """These modules were visible to every HMS hospital before they became
    licensable, so existing HMS tenants keep them. CRM-only tenants (no HMS
    module) never saw them and stay as they are; an empty list already
    means "everything"."""
    Hospital = apps.get_model("core", "Hospital")
    for hospital in Hospital.objects.all().only("id", "enabled_modules"):
        modules = list(hospital.enabled_modules or [])
        if not modules or not any(m in CORE_HMS_MODULES for m in modules):
            continue
        missing = [m for m in EXTENDED_HMS_MODULES if m not in modules]
        if missing:
            hospital.enabled_modules = modules + missing
            hospital.save(update_fields=["enabled_modules"])


def remove(apps, schema_editor):
    Hospital = apps.get_model("core", "Hospital")
    for hospital in Hospital.objects.all().only("id", "enabled_modules"):
        modules = list(hospital.enabled_modules or [])
        kept = [m for m in modules if m not in EXTENDED_HMS_MODULES]
        if kept != modules:
            hospital.enabled_modules = kept
            hospital.save(update_fields=["enabled_modules"])


class Migration(migrations.Migration):
    dependencies = [("core", "0010_hospital_helpline")]
    operations = [migrations.RunPython(enable_for_hms_hospitals, remove)]
