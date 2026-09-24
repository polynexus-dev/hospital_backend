from django.db import migrations


def seed(apps, schema_editor):
    from apps.quality.defaults import seed_hospital

    for hospital in apps.get_model("core", "Hospital").objects.all():
        seed_hospital(hospital, get_model=apps.get_model)


class Migration(migrations.Migration):
    dependencies = [("quality", "0001_initial"), ("core", "__latest__")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
