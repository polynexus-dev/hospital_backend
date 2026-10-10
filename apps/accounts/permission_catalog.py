"""Which permissions a hospital may use, and who may hand them out.

Three tiers, each bounded by the one above it:

  SaaS admin      sets Hospital.permission_ceiling — what the hospital may
                  use at all (on top of Hospital.enabled_modules).
  Hospital admin  (owner / admin / hospital_administrator role) holds every
                  permission under that ceiling and can grant any of them.
  Anyone else     with accounts.change_role / change_user can only grant
                  permissions they hold themselves — no one hands out more
                  than they have.

apps.accounts.backends.HospitalPermissionBackend enforces the ceiling on
every has_perm() check, so lowering it takes effect on the next request
without rewriting any role; raising it again restores what roles had.
"""
from collections import defaultdict
from functools import lru_cache

from django.apps import apps as django_apps
from django.contrib.auth.models import Permission
from django.db.models.signals import post_migrate
from rest_framework.exceptions import ValidationError

ADMIN_TEMPLATES = ("owner", "admin", "hospital_administrator")
STANDARD_VERBS = ("add", "view", "change", "delete")

# Platform-side models: managed through SaaS capabilities, never by a hospital.
HIDDEN_MODELS = {
    "saas_admin.tenantsubscription",
    "saas_admin.tenantinvoice",
    "saas_admin.tenantusagesnapshot",
    "saas_admin.invoicesequence",
}


@lru_cache(maxsize=1)
def _catalog_rows():
    """((code, app_label, model, codename, name, pk), ...) for every
    grantable permission. Permissions only change on migrate, which clears this."""
    labels = [cfg.label for cfg in django_apps.get_app_configs() if cfg.name.startswith("apps.")]
    rows = Permission.objects.filter(content_type__app_label__in=labels).values_list(
        "content_type__app_label", "content_type__model", "codename", "name", "pk"
    )
    return tuple(
        (f"{app}.{codename}", app, model, codename, name, pk)
        for app, model, codename, name, pk in rows.order_by("content_type__app_label", "content_type__model", "codename")
        if f"{app}.{model}" not in HIDDEN_MODELS
    )


def _clear_caches(**kwargs):
    _catalog_rows.cache_clear()
    _bounds.cache_clear()


post_migrate.connect(_clear_caches, dispatch_uid="accounts.permission_catalog.clear")


def catalog_codes():
    return {row[0] for row in _catalog_rows()}


def _module_for_app(app_label):
    from apps.core.modules import VIEW_MODULES

    return VIEW_MODULES.get(f"apps.{app_label}")


@lru_cache(maxsize=512)
def _bounds(enabled_modules, ceiling):
    """(allowed, blocked) code sets for a hospital's modules (None = every
    module) + ceiling."""
    allowed = set()
    for code, app, *_ in _catalog_rows():
        module = _module_for_app(app)
        if module is not None and enabled_modules is not None and module not in enabled_modules:
            continue
        allowed.add(code)
    if ceiling is not None:
        allowed &= set(ceiling)
    return frozenset(allowed), frozenset(catalog_codes() - allowed)


def hospital_bounds(hospital, *, ignore_ceiling=False):
    """(allowed, blocked) for `hospital`, within its effective modules
    (apps.core.modules.effective_modules)."""
    from apps.core.modules import effective_modules

    ceiling = None if ignore_ceiling or hospital.permission_ceiling is None else tuple(sorted(hospital.permission_ceiling))
    modules = effective_modules(hospital)
    return _bounds(None if modules is None else tuple(sorted(modules)), ceiling)


def is_hospital_admin(user) -> bool:
    role = getattr(user, "role", None)
    return bool(role and role.template in ADMIN_TEMPLATES)


def _unbounded(user) -> bool:
    return user.is_superuser or getattr(user, "can_cross_tenant", False) or not user.hospital_id


def blocked_for(user) -> frozenset:
    """Permissions `user`'s hospital doesn't allow, whatever their role says."""
    if _unbounded(user):
        return frozenset()
    if not hasattr(user, "_hospital_blocked_perms"):
        user._hospital_blocked_perms = hospital_bounds(user.hospital)[1]
    return user._hospital_blocked_perms


def grantable_by(user, hospital) -> set:
    """What `user` may grant to roles and users of `hospital`."""
    allowed = set(hospital_bounds(hospital)[0])
    if user.is_superuser or getattr(user, "can_cross_tenant", False):
        return allowed
    if user.hospital_id != hospital.pk:
        return set()
    if is_hospital_admin(user):
        return allowed
    return allowed & user.get_all_permissions()


def role_assignment_error(assigner, role, target=None):
    """Why `assigner` may not give `role` to `target` (None = allowed).

    Assigning a role hands over its permissions, so the same rule as editing
    one applies: only what the assigner could grant themselves. Hospital
    admin roles — and changing an existing hospital admin — are reserved for
    hospital admins."""
    if assigner.is_superuser or getattr(assigner, "can_cross_tenant", False):
        return None
    hospital_id = target.hospital_id if target is not None and target.hospital_id else assigner.hospital_id
    if role is not None and role.hospital_id != hospital_id:
        return "That role belongs to a different hospital."
    if is_hospital_admin(assigner):
        return None
    if target is not None and is_hospital_admin(target):
        return "Only a hospital admin can change a hospital admin's account."
    if role is None:
        return None
    if role.template in ADMIN_TEMPLATES:
        return "Only a hospital admin can assign a hospital admin role."
    beyond = (codes_of(role.group.permissions) & catalog_codes()) - grantable_by(assigner, role.hospital) - blocked_for(assigner)
    if beyond:
        return f"This role includes permissions you don't have yourself (e.g. {sorted(beyond)[0]})."
    return None


def apply_grant(current, submitted, grantable):
    """The permission set to save when an editor who may grant `grantable`
    submits `submitted` for something that currently has `current`.

    Permissions outside the editor's reach are left exactly as they were —
    a department head editing a role can't strip what only the hospital
    admin could have given it, nor add anything they don't hold."""
    current, submitted = set(current), set(submitted)
    unknown = submitted - catalog_codes()
    if unknown:
        raise ValidationError({"permissions": [f"Unknown permission: {c}" for c in sorted(unknown)[:10]]})
    beyond = submitted - current - grantable
    if beyond:
        raise ValidationError({"permissions": [f"You can't grant {c}" for c in sorted(beyond)[:10]]})
    return (submitted & grantable) | (current - grantable)


def permission_objects(codes):
    by_code = {row[0]: row[5] for row in _catalog_rows()}
    return Permission.objects.filter(pk__in=[by_code[c] for c in codes if c in by_code])


def codes_of(permissions_qs):
    return {f"{app}.{codename}" for app, codename in permissions_qs.values_list("content_type__app_label", "codename")}


def catalog(codes):
    """The UI tree for `codes`: apps → models → standard verbs + other actions."""
    wanted = set(codes)
    tree = defaultdict(lambda: defaultdict(lambda: {"perms": {}, "other": []}))
    for code, app, model, codename, name, _pk in _catalog_rows():
        if code not in wanted:
            continue
        entry = tree[app][model]
        verb = codename.split("_", 1)[0]
        if verb in STANDARD_VERBS and codename == f"{verb}_{model}":
            entry["perms"][verb] = code
        else:
            entry["other"].append({"code": code, "label": name})

    result = []
    for app, models in tree.items():
        config = django_apps.get_app_config(app)
        model_rows = []
        for model, entry in models.items():
            try:
                label = str(config.get_model(model)._meta.verbose_name)
            except LookupError:
                label = model
            model_rows.append({"model": model, "label": label[:1].upper() + label[1:], **entry})
        model_rows.sort(key=lambda m: m["label"])
        result.append({"app": app, "label": str(config.verbose_name).title(), "module": _module_for_app(app), "models": model_rows})
    result.sort(key=lambda a: a["label"])
    return result
