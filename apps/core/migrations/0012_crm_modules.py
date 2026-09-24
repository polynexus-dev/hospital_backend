from django.db import migrations

CRM_MODULES = ["telephony", "enquiries", "inbox", "referrals", "packages", "tpa", "feedback", "workflows"]


def enable_crm(apps, schema_editor):
    """Every hospital could use the CRM before it became licensable, so each
    keeps all of it; the old single "crm" flag becomes the eight CRM modules.
    An empty list already means "everything"."""
    Hospital = apps.get_model("core", "Hospital")
    for hospital in Hospital.objects.all().only("id", "enabled_modules"):
        modules = list(hospital.enabled_modules or [])
        if not modules:
            continue
        updated = [m for m in modules if m != "crm"] + [m for m in CRM_MODULES if m not in modules]
        if updated != modules:
            hospital.enabled_modules = updated
            hospital.save(update_fields=["enabled_modules"])


def restore_flag(apps, schema_editor):
    Hospital = apps.get_model("core", "Hospital")
    for hospital in Hospital.objects.all().only("id", "enabled_modules"):
        modules = list(hospital.enabled_modules or [])
        if any(m in CRM_MODULES for m in modules):
            hospital.enabled_modules = [m for m in modules if m not in CRM_MODULES] + ["crm"]
            hospital.save(update_fields=["enabled_modules"])


class Migration(migrations.Migration):
    dependencies = [("core", "0011_extended_hms_modules")]
    operations = [migrations.RunPython(enable_crm, restore_flag)]
