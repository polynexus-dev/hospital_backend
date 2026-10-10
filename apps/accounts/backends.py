from django.contrib.auth.backends import ModelBackend

from .permission_catalog import blocked_for
from .permission_templates import BASELINE_VIEW_CODES


class HospitalPermissionBackend(ModelBackend):
    """ModelBackend, bounded by the hospital's SaaS permission ceiling
    (see apps.accounts.permission_catalog).

    Everyone keeps their role + direct permissions minus anything the
    ceiling removes; a hospital user with no role at all gets the baseline
    reference-data reads. (Hospital admins aren't widened here: templates
    such as hospital_administrator deliberately withhold clinical detail;
    their CRUD bypass lives in RoleBasedModelPermissions.) Every has_perm() in the codebase —
    DRF permission classes, the /users/me/ permission list the frontend
    gates its menu on — goes through here."""

    def get_all_permissions(self, user_obj, obj=None):
        if not user_obj.is_active or user_obj.is_anonymous or obj is not None:
            return set()
        if not hasattr(user_obj, "_hospital_perm_cache"):
            perms = set(super().get_all_permissions(user_obj, obj))
            if user_obj.hospital_id and not user_obj.role_id:
                perms |= BASELINE_VIEW_CODES  # "staff accounts with no role have basic read access"
            user_obj._hospital_perm_cache = perms - blocked_for(user_obj)
        return user_obj._hospital_perm_cache
