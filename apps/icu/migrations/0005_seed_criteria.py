from django.db import migrations


def seed(apps, schema_editor):
    from apps.icu.workflow import seed_criteria

    for hospital in apps.get_model("core", "Hospital").objects.all():
        seed_criteria(hospital, get_model=apps.get_model)


class Migration(migrations.Migration):
    dependencies = [("icu", "0004_icuadmission_admission_criteria_met_and_more"), ("core", "__latest__")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
