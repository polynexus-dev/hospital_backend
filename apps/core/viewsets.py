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

    def get_queryset(self):
        manager = self.queryset.model._default_manager
        user = self.request.user
        if user.is_staff and self.request.headers.get("X-Hospital-Id"):
            hospital_id = self.request.headers["X-Hospital-Id"]
        else:
            hospital_id = getattr(user, "hospital_id", None)
        if hospital_id is None:
            return manager.none()
        return manager.filter(hospital_id=hospital_id)

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        if hospital is None:
            raise ValidationError("The requesting user is not attached to a hospital.")
        serializer.save(hospital=hospital)
