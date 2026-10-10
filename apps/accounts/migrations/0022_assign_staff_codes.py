"""Give existing Polynexus staff (is_saas_admin) their permanent staff code;
new staff get one when their account is saved (User.save)."""
from django.db import migrations


def assign(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for user in User.objects.filter(is_saas_admin=True, staff_code__isnull=True):
        User.objects.filter(pk=user.pk).update(staff_code=f"PNX-{user.pk:04d}")


class Migration(migrations.Migration):
    dependencies = [("accounts", "0021_staff_code_and_licence_right")]
    operations = [migrations.RunPython(assign, migrations.RunPython.noop)]
