from django.db import migrations


def seed(apps, schema_editor):
    from apps.mrd.icd10_seed import seed as do_seed

    do_seed(apps.get_model("mrd", "ICD10Code"))


class Migration(migrations.Migration):
    dependencies = [("mrd", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
