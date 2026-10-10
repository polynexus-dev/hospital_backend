"""Reads now require `view_<model>` (apps.core.permissions.RoleBasedModelPermissions).
Before, any role could read everything, so give existing roles and direct
grants what they were relying on: `view` on every model they can write, plus
the reference data every screen reads (permission_templates.BASELINE_VIEW_MODELS
— copied here so later edits to that list don't change this migration)."""
from django.db import migrations
from django.db.models import Q

BASELINE_VIEW_MODELS = [
    ("core", "department"),
    ("accounts", "user"),
    ("accounts", "role"),
    ("appointments", "doctor"),
    ("appointments", "slot"),
    ("laboratory", "labtest"),
    ("pharmacy", "medicine"),
    ("radiology", "radiologyprocedure"),
]


def grant_views(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Role = apps.get_model("accounts", "Role")
    User = apps.get_model("accounts", "User")

    views = {(p.content_type_id, p.codename): p for p in Permission.objects.filter(codename__startswith="view_").select_related("content_type")}
    baseline_q = Q(pk__in=[])
    for app, model in BASELINE_VIEW_MODELS:
        baseline_q |= Q(content_type__app_label=app, content_type__model=model, codename=f"view_{model}")
    baseline = list(Permission.objects.filter(baseline_q))

    def missing_views(perms):
        out = []
        for p in perms.select_related("content_type"):
            view = views.get((p.content_type_id, f"view_{p.content_type.model}"))
            if view is not None:
                out.append(view)
        return out

    for role in Role.objects.exclude(group=None).select_related("group"):
        role.group.permissions.add(*missing_views(role.group.permissions.all()), *baseline)
    for user in User.objects.filter(user_permissions__isnull=False).distinct():
        user.user_permissions.add(*missing_views(user.user_permissions.all()))


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0019_user_saas_role"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [migrations.RunPython(grant_views, migrations.RunPython.noop)]
