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
    map directly onto Django's add/change/delete model permissions."""

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


class ActionPermissionRequired(BasePermission):
    """For custom @action endpoints that need a specific, named permission
    beyond the add/change/delete/view Django auto-generates."""

    def has_permission(self, request, view):
        action_permissions = getattr(view, "action_permissions", None)
        if not action_permissions:
            return True
        required = action_permissions.get(getattr(view, "action", None))
        if required is None:
            return True
        return request.user.has_perm(required)


class RequiresClinicalDetailPermission(BasePermission):
    """Blocks every action on a ViewSet whose entire content is clinical
    for any role without patients.access_clinical_detail."""

    def has_permission(self, request, view):
        return request.user.has_perm("patients.access_clinical_detail")


class IsSaaSAdmin(BasePermission):
    """Gates the platform-management surface (apps.saas_admin)."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and (user.is_superuser or getattr(user, "is_saas_admin", False)))


class HospitalActive(BasePermission):
    """Global, always-on: 403s any request from a user whose hospital has
    been suspended (Hospital.is_active=False)."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return True
        if user.is_staff or user.is_superuser:
            return True
        hospital = getattr(user, "hospital", None)
        return hospital is None or hospital.is_active
