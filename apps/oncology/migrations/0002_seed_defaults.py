from django.db import migrations


def seed(apps, schema_editor):
    from apps.oncology.views import seed_protocols

    for hospital in apps.get_model("core", "Hospital").objects.all():
        seed_protocols(hospital, get_model=apps.get_model)


class Migration(migrations.Migration):
    dependencies = [("oncology", "0001_initial"), ("core", "0009_alter_auditlog_action")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
