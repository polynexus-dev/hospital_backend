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


class RequiresViewPermission(BasePermission):
    """Gates `list`/`retrieve` on Django's `view_<model>` permission —
    RoleBasedModelPermissions deliberately does not (see that class's
    read/write asymmetry, and apps.core.tests.
    test_restricted_role_can_still_list_and_retrieve_patients: most roles'
    screens need to read records — e.g. Patient — from an app they have no
    write access to). That default is wrong for an app that's meant to be
    read-restricted too, not just write-restricted — apps.accounts.
    permission_templates.PERMISSION_TEMPLATES only ever lists "privacy" in
    FULL_ACCESS_APPS (owner/admin/hospital_administrator); every other
    template omits it entirely, meaning no other role is ever granted
    `privacy.view_*` — so this class's check reflects a restriction the
    templates already encode, it doesn't invent a new one. Opt-in per
    ViewSet (like RequiresClinicalDetailPermission) rather than a change to
    the shared default, so it only tightens reads where a ViewSet's own
    permission_classes says so."""

    def has_permission(self, request, view):
        if getattr(view, "action", None) not in ("list", "retrieve"):
            return True
        queryset = getattr(view, "queryset", None)
        if queryset is None:
            return True
        model_cls = queryset.model
        permission = f"{model_cls._meta.app_label}.view_{model_cls._meta.model_name}"
        return request.user.has_perm(permission)


class IsSaaSAdmin(BasePermission):
    """Gates the platform-management surface (apps.saas_admin)."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if getattr(user, "is_saas_admin", False):
            return True
        if user.is_superuser and not getattr(user, "hospital_id", None):
            return True
        return False


class CanReviewEmergencyAccess(BasePermission):
    """Gates apps.core.views.EmergencyAccessLogViewSet (Part A #6 —
    break-glass access "must be flagged and reviewable"). Deliberately
    broader than IsAdminUser: an auditor/admin role is meant to review
    this *within* their own hospital without needing platform-staff
    (is_staff) elevation — this is the hospital's own compliance
    oversight, not a platform-ops concern."""

    REVIEWER_TEMPLATES = {"owner", "admin", "hospital_administrator", "hospital_auditor", "crm_auditor", "crm_super_admin"}

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if user.is_staff or user.is_superuser:
            return True
        role = getattr(user, "role", None)
        return bool(role and role.template in self.REVIEWER_TEMPLATES)


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
