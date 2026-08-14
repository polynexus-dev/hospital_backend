from rest_framework.exceptions import ValidationError


class TenantScopedViewSetMixin:
    """Scopes every action (list/retrieve/update/partial_update/destroy via
    get_queryset, create via perform_create) to the requesting user's
    hospital.

    Two separate bugs had to be fixed here, not one:

    1. get_queryset() must not touch `self.queryset` (the class-body
       `Model.objects.all()` attribute) at all — that expression is
       evaluated exactly once, at module-import time, before any request
       exists. DRF's default get_queryset() just does `self.queryset.all()`,
       which clones that frozen (unfiltered) query rather than re-invoking
       the manager, so relying on it silently drops tenant scoping on every
       action except create. Going through
       `self.queryset.model._default_manager` instead builds a query fresh,
       per request.

    2. That fresh query must be filtered using `self.request.user.hospital_id`
       directly — NOT `apps.core.tenancy.get_current_hospital_id()`. That
       contextvar is populated by TenantMiddleware, a plain Django
       middleware that reads `request.user` *before* DRF has resolved
       authentication (JWT/session auth only happens once DRF's Request
       wrapper is accessed, inside the view). For this project's
       JWTAuthentication-only frontend, `request.user` is still
       AnonymousUser at the point TenantMiddleware runs, so the contextvar
       is always unset for real API traffic — using it here would silently
       serve nobody anything (fail-closed on every single request) rather
       than fixing the leak. `self.request.user` inside get_queryset(),
       by contrast, is DRF's own resolved user (JWT included) — the same
       thing the already-correct UserViewSet/RoleViewSet and the analytics
       APIViews rely on. Mirror that pattern here instead of the
       contextvar.

    Staff `X-Hospital-Id` override: reads the header directly off
    `self.request`, the same fix as #2 above — TenantMiddleware setting the
    tenancy contextvar from that header has the identical JWT-timing
    problem, so a staff user's header-selected hospital was silently not
    taking effect for any of these ~30 viewsets (UserViewSet/RoleViewSet
    were unaffected only because they implement this same check directly
    in their own get_queryset() overrides, bypassing the shared mixin
    entirely). Read access only — perform_create below still stamps the
    creating staff user's own hospital, matching UserViewSet/RoleViewSet's
    existing behavior (their own perform_create is the inherited one from
    this mixin too, so this isn't a new asymmetry, just a pre-existing one
    this fix doesn't change)."""

    # Set on a subclass to a query-filter path (e.g.
    # "admission__bed__ward__assigned_nurses") to additionally restrict
    # the queryset to records assigned to the requesting user, but only
    # for roles with data_scope="assigned_only" (see
    # docs/erp/03-rbac-and-roles.md §2b). None = no extra scoping, the
    # default for every existing CRM viewset — this is opt-in per
    # ViewSet, not a behavior change for anything that doesn't set it.
    assignment_scope_field = None

    def get_queryset(self):
        manager = self.queryset.model._default_manager
        user = self.request.user
        if user.is_staff and self.request.headers.get("X-Hospital-Id"):
            hospital_id = self.request.headers["X-Hospital-Id"]
        else:
            hospital_id = getattr(user, "hospital_id", None)
        if hospital_id is None:
            return manager.none()
        queryset = manager.filter(hospital_id=hospital_id)
        role = getattr(user, "role", None)
        if self.assignment_scope_field and role is not None and role.data_scope == "assigned_only":
            queryset = queryset.filter(**{self.assignment_scope_field: user})
        return queryset

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        if hospital is None:
            raise ValidationError("The requesting user is not attached to a hospital.")
        serializer.save(hospital=hospital)


class AuditedModelViewSetMixin:
    """Wires apps.core.audit.log_action() field-level diffs into a
    ViewSet's create/update/destroy — see
    docs/erp/07-audit-and-security.md §2a. Lives here, not as a
    Model.save() hook, because log_action() needs `actor`/`request`, and
    this project already learned (see TenantScopedViewSetMixin's
    docstring above) that a contextvar populated by TenantMiddleware can't
    reliably supply the authenticated user at model-save time — JWT auth
    resolves later than that middleware runs. `self.request.user` inside a
    ViewSet method is the same already-resolved user
    TenantScopedViewSetMixin itself relies on; this mixin uses that same,
    known-correct source instead of repeating the mistake.

    Only diffs fields listed in `audited_fields` on the ViewSet — logging
    every field on every model by default would bury the sensitive-field
    diffs (a changed diagnosis) in noise from routine ones (an updated
    `updated_at`-adjacent field)."""

    audited_fields: tuple = ()

    def _log(self, action, instance, old=None):
        from apps.core.audit import log_action

        changes = {}
        if action == "update" and old is not None:
            for field in self.audited_fields:
                old_value = getattr(old, field)
                new_value = getattr(instance, field)
                if old_value != new_value:
                    changes[field] = {"old": str(old_value), "new": str(new_value)}
        elif action == "create":
            changes = {field: str(getattr(instance, field)) for field in self.audited_fields}

        if action == "update" and not changes:
            return  # nothing audited-worthy changed — don't write a no-op entry
        log_action(actor=self.request.user, action=action, instance=instance, changes=changes, request=self.request)

    def perform_create(self, serializer):
        super().perform_create(serializer)
        self._log("create", serializer.instance)

    def perform_update(self, serializer):
        old = type(serializer.instance).objects.get(pk=serializer.instance.pk)
        super().perform_update(serializer)
        self._log("update", serializer.instance, old=old)

    def perform_destroy(self, instance):
        self._log("delete", instance)
        super().perform_destroy(instance)
