from rest_framework.permissions import BasePermission

_ACTION_PERM_VERB = {
    "create": "add",
    "update": "change",
    "partial_update": "change",
    "destroy": "delete",
}


class RoleBasedModelPermissions(BasePermission):
    """Enforces the requesting user's Role -> Django Group permissions
    (see apps.accounts.permission_templates) on the four REST verbs that
    map directly onto Django's add/change/delete model permissions.

    Deliberately narrow in scope:
    - Only `create`/`update`/`partial_update`/`destroy` are checked. `list`/
      `retrieve` stay open to any authenticated, tenant-scoped user — every
      role in this product needs to *see* records relevant to their
      screens; restriction here is about who can mutate data, matching how
      the roles are described in the product manual.
    - Custom `@action` methods (check-in, claim, resolve, switch-hospital,
      change_password, ...) are intentionally NOT covered — DRF's `action`
      attribute is the method name for those, which never appears in
      `_ACTION_PERM_VERB`, so this permission defers (returns True) and
      leaves them to their own explicit authorization logic. Several
      already have their own checks (e.g. UserViewSet.switch_hospital's
      is_staff gate); folding them into a generic "requires change_user"
      check would incorrectly gate legitimate self-service actions (a
      front-desk user changing their own password isn't "changing a User"
      in the admin-CRUD sense the Django permission represents).
    - Views without a `queryset` (plain APIViews — analytics/telephony
      reports, FHIR/CSV exports, the AI chat endpoint, webhooks) have
      nothing to check against and are left to IsAuthenticated /
      AllowAny / IsAdminUser as already declared on each view.

    Superusers bypass via Django's own `has_perm()` (always True for
    `is_superuser`), consistent with the rest of Django.
    """

    def has_permission(self, request, view):
        perm_verb = _ACTION_PERM_VERB.get(getattr(view, "action", None))
        if perm_verb is None:
            return True

        queryset = getattr(view, "queryset", None)
        if queryset is None:
            return True

        model_cls = queryset.model
        permission = f"{model_cls._meta.app_label}.{perm_verb}_{model_cls._meta.model_name}"
        return request.user.has_perm(permission)
