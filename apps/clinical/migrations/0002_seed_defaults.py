from django.db import migrations


def seed(apps, schema_editor):
    from apps.clinical.defaults import seed_hospital

    for hospital in apps.get_model("core", "Hospital").objects.all():
        seed_hospital(hospital, get_model=apps.get_model)


class Migration(migrations.Migration):
    dependencies = [("clinical", "0001_initial"), ("core", "0009_alter_auditlog_action")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
