"""Grant the new manage-inactive-insurance permission to existing roles whose
template gets them (permission_templates.VERIFY_PERM_ROLES). The role sync
only adds permissions for models a role has never had, so permissions added
to an existing model (InsurancePolicy) need this."""
from django.db import migrations

GRANTS = {
    "manage_inactive_insurance": {"owner", "admin", "hospital_administrator", "billing_manager", "finance_manager", "insurance_tpa_executive"},
}


def grant(apps, schema_editor):
    from django.contrib.auth.management import create_permissions

    config = apps.get_app_config("finance")
    config.models_module = True  # historical app configs have none; create_permissions skips those
    create_permissions(config, apps=apps, verbosity=0)
    config.models_module = None
    Permission = apps.get_model("auth", "Permission")
    Role = apps.get_model("accounts", "Role")
    for codename, templates in GRANTS.items():
        perm = Permission.objects.filter(content_type__app_label="finance", codename=codename).first()
        if perm is None:
            continue
        for role in Role.objects.filter(template__in=templates).exclude(group=None).select_related("group"):
            role.group.permissions.add(perm)


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0011_alter_insurancepolicy_options_and_more"),
        ("accounts", "0024_emr_parity"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
