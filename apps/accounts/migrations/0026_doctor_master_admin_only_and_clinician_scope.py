"""Existing roles: (1) only hospital-admin roles keep add/change/delete on
doctor profiles — the "appointments" app permissions had silently included
them, so doctors, front desk and CRM roles could create doctors; (2) doctor,
surgeon and anaesthetist roles see only their own appointments and patients
(data_scope=assigned_only). Admins can widen a role again from Roles & RBAC."""
from django.db import migrations

ADMIN_TEMPLATES = ("owner", "admin", "hospital_administrator")
DOCTOR_MASTER = ("add_doctor", "change_doctor", "delete_doctor")
OWN_RECORDS = ("doctor", "surgeon", "anaesthetist")


def forward(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Permission = apps.get_model("auth", "Permission")
    perms = list(Permission.objects.filter(content_type__app_label="appointments", codename__in=DOCTOR_MASTER))
    if perms:
        for role in Role.objects.exclude(template__in=ADMIN_TEMPLATES).select_related("group"):
            if role.group_id:
                role.group.permissions.remove(*perms)
    Role.objects.filter(template__in=OWN_RECORDS, data_scope="all").update(data_scope="assigned_only")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0025_user_must_change_password"),
        ("appointments", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
