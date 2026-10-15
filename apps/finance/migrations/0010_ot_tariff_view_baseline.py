"""Every role reads surgery grades and OT tariffs (they're baseline
reference data, permission_templates.BASELINE_VIEW_MODELS): grant view on
them to the roles that already exist."""
from django.db import migrations

MODELS = ("surgerygrade", "ottariff")


def grant(apps, schema_editor):
    from django.contrib.auth.management import create_permissions

    config = apps.get_app_config("finance")
    config.models_module = True  # historical app configs have none; create_permissions skips those
    create_permissions(config, apps=apps, verbosity=0)
    config.models_module = None
    Permission = apps.get_model("auth", "Permission")
    Role = apps.get_model("accounts", "Role")
    perms = list(Permission.objects.filter(content_type__app_label="finance", codename__in=[f"view_{m}" for m in MODELS]))
    for role in Role.objects.exclude(group=None).select_related("group"):
        role.group.permissions.add(*perms)


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0009_surgerygrade_ottariff_and_more"),
        ("accounts", "0024_emr_parity"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
