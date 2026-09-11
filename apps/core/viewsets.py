from rest_framework.exceptions import ValidationError


class TenantScopedViewSetMixin:
    """Scopes every action (list/retrieve/update/partial_update/destroy via
    get_queryset, create via perform_create) to the requesting user's
    hospital."""

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
        if self.assignment_scope_field and role is not None and getattr(role, "data_scope", None) == "assigned_only":
            queryset = queryset.filter(**{self.assignment_scope_field: user})
        return queryset

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        if hospital is None:
            raise ValidationError("The requesting user is not attached to a hospital.")
        serializer.save(hospital=hospital)


class SoftDeleteViewSetMixin:
    """For viewsets over a apps.core.models.SoftDeleteModel — routes DELETE
    through `.delete(actor=user, reason=...)` so the soft-delete metadata is
    stamped, rather than DRF's default `instance.delete()` which would call
    the underlying delete without actor attribution."""

    def perform_destroy(self, instance):
        reason = self.request.query_params.get("reason", "") or self.request.data.get("reason", "") if isinstance(self.request.data, dict) else ""
        instance.delete(actor=self.request.user, reason=reason)


class AuditedModelViewSetMixin:
    """Wires apps.core.audit.log_action() field-level diffs into a
    ViewSet's create/update/destroy."""

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
            return
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
