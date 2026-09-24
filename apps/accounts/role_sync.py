"""Keep template-based roles up to date as the product grows.

A role's permissions are applied from its template when the role is created.
Features added later (new apps or new models) would otherwise never reach
existing roles — a hospital owner simply wouldn't see them. This grants a
role its template's permissions for models it has never been synced for,
and records what it handled, so a permission an administrator deliberately
removed from a role is never granted back.
"""
from collections import defaultdict

from django.contrib.auth.models import Permission


def _template_permissions_by_model(template):
    """{"app.model": [Permission, ...]} the template grants."""
    from .permission_templates import template_permissions

    by_model = defaultdict(list)
    for p in template_permissions(template):
        by_model[f"{p.content_type.app_label}.{p.content_type.model}"].append(p)
    return by_model


def sync_role(role, cache=None):
    """Returns how many permissions were granted."""
    if not role.template or not role.group_id:
        return 0
    cache = cache if cache is not None else {}
    if role.template not in cache:
        cache[role.template] = _template_permissions_by_model(role.template)
    by_model = cache[role.template]
    if not by_model:
        return 0
    if role.template_synced_models:
        handled = set(role.template_synced_models)
    else:  # roles created before this was tracked: a model it has any permission on counts as handled
        handled = {
            f"{app}.{model}"
            for app, model in Permission.objects.filter(group=role.group).values_list("content_type__app_label", "content_type__model")
        }
    new = [p for key, perms in by_model.items() if key not in handled for p in perms]
    if new:
        role.group.permissions.add(*new)
    synced = sorted(handled | set(by_model))
    if synced != sorted(role.template_synced_models or []):
        type(role).objects.filter(pk=role.pk).update(template_synced_models=synced)
        role.template_synced_models = synced
    return len(new)


def sync_all_roles(stdout=None):
    from .models import Role

    cache, granted, roles = {}, 0, 0
    for role in Role.objects.exclude(template="").exclude(template__isnull=True).select_related("group"):
        n = sync_role(role, cache)
        granted += n
        roles += bool(n)
    if stdout:
        stdout.write(f"Role permission sync: granted {granted} permission(s) across {roles} role(s).")
    return granted
