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


class ActionPermissionRequired(BasePermission):
    """For custom @action endpoints that need a specific, named permission
    beyond the add/change/delete/view Django auto-generates — e.g. "verify"
    on LabResult, "finalize" on a clinical note, "approve_discount" on a
    Bill (see docs/erp/03-rbac-and-roles.md §2a). Opt-in per action:

        class LabResultViewSet(viewsets.ModelViewSet):
            action_permissions = {"verify": "laboratory.verify_labresult"}

            @action(detail=True, methods=["post"])
            def verify(self, request, pk=None):
                ...

    A ViewSet (or action) that doesn't declare an entry in
    `action_permissions` is left alone (returns True) — this only ever
    adds a check where one is explicitly wired up, matching
    RoleBasedModelPermissions' existing philosophy of not guessing at
    authorization for actions it doesn't know about. Registered globally
    in DEFAULT_PERMISSION_CLASSES precisely because it's a safe no-op
    everywhere it isn't configured."""

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
    (diagnosis, medications — apps.patients.Prescription today; Encounter/
    Diagnosis/ClinicalNote once the ERP OPD module ships) for any role
    without patients.access_clinical_detail. Unlike Patient itself (which
    has a CRM-safe demographic/contact subset — see
    apps.patients.views.PatientViewSet.get_serializer_class), there's no
    partial view of a prescription that makes sense to show a role that
    shouldn't see clinical content at all, so this gates the whole
    ViewSet rather than swapping serializers."""

    def has_permission(self, request, view):
        return request.user.has_perm("patients.access_clinical_detail")


class IsSaaSAdmin(BasePermission):
    """Gates the platform-management surface (apps.saas_admin) — tenant
    subscriptions/invoices/usage, cross-tenant support-ticket resolution,
    platform analytics. Deliberately narrower than IsAdminUser/is_staff:
    is_staff already means "this account gets the existing cross-hospital
    X-Hospital-Id override" (TenantScopedViewSetMixin, UserViewSet.
    switch_hospital) and pre-dates this permission, but seeing platform
    billing/revenue data is a step beyond that. User.is_saas_admin forces
    is_staff=True on save() (apps.accounts.models.User.save), so a SaaS
    admin keeps every existing is_staff capability for free — this class
    only ever narrows further, it never has to replace an is_staff check
    anywhere else in the codebase."""

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and (user.is_superuser or getattr(user, "is_saas_admin", False)))


class HospitalActive(BasePermission):
    """Global, always-on: 403s any request from a user whose hospital has
    been suspended (Hospital.is_active=False via HospitalViewSet.
    toggle_status), closing the gap where suspending a tenant updated the
    flag but nothing actually checked it on the request path (every
    TenantScopedViewSetMixin-based ViewSet and the hand-rolled
    UserViewSet/RoleViewSet equivalents only ever filtered by hospital_id,
    never by hospital.is_active). Staff/SaaS-admins are exempt — they're
    the ones who need to keep working *inside* a suspended hospital (via
    X-Hospital-Id) to investigate or reactivate it. A user with no
    hospital at all (staff-only accounts) is also exempt — there's nothing
    to be suspended."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return True  # not this permission's job — IsAuthenticated handles it
        if user.is_staff or user.is_superuser:
            return True
        hospital = getattr(user, "hospital", None)
        return hospital is None or hospital.is_active
