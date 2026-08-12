from rest_framework.exceptions import ValidationError


class TenantScopedViewSetMixin:
    """Scopes create/list to the requesting user's hospital. Queryset
    filtering itself is handled by TenantManager (via TenantMiddleware);
    this mixin only stamps `hospital` on create, since that can't be
    inferred from the queryset."""

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        if hospital is None:
            raise ValidationError("The requesting user is not attached to a hospital.")
        serializer.save(hospital=hospital)
