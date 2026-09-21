from rest_framework.exceptions import ValidationError

EMERGENCY_REASON_HEADER = "X-Emergency-Reason"


class TenantScopedViewSetMixin:
    """Scopes every action (list/retrieve/update/partial_update/destroy via
    get_queryset, create via perform_create) to the requesting user's
    hospital.

    Break-glass (Part A #6): a `data_scope=assigned_only` user (see
    apps.accounts.models.Role) who supplies a non-empty X-Emergency-Reason
    header on a `retrieve` for a specific record their assignment_scope_field
    would otherwise hide gets it anyway — real emergencies don't wait for a
    reassignment. This only ever engages for a record genuinely outside the
    user's normal scope (never fires, and never logs, for a record they'd
    see anyway) and only for `retrieve` — never `list`, so this can't be
    used to browse the whole hospital's records "just in case"; it grants
    access to one already-identified record at a time. Every real bypass
    writes an apps.core.models.EmergencyAccessLog row for later review."""

    assignment_scope_field = None

    def get_queryset(self):
        manager = self.queryset.model._default_manager
        user = self.request.user
        if user.can_cross_tenant and self.request.headers.get("X-Hospital-Id"):
            hospital_id = self.request.headers["X-Hospital-Id"]
        else:
            hospital_id = getattr(user, "hospital_id", None)
        if hospital_id is None:
            return manager.none()

        hospital_queryset = manager.filter(hospital_id=hospital_id)
        role = getattr(user, "role", None)
        if not (self.assignment_scope_field and role is not None and getattr(role, "data_scope", None) == "assigned_only"):
            return hospital_queryset

        scoped_queryset = hospital_queryset.filter(**{self.assignment_scope_field: user})
        reason = self.request.headers.get(EMERGENCY_REASON_HEADER, "").strip()
        if self.action == "retrieve" and reason:
            pk = self.kwargs.get(self.lookup_url_kwarg or self.lookup_field)
            if pk is not None and hospital_queryset.filter(pk=pk).exists() and not scoped_queryset.filter(pk=pk).exists():
                self._log_emergency_access(hospital_id, pk, reason)
                return hospital_queryset
        return scoped_queryset

    def _log_emergency_access(self, hospital_id, object_id, reason):
        from apps.core.models import EmergencyAccessLog

        EmergencyAccessLog.objects.create(
            hospital_id=hospital_id,
            actor=self.request.user,
            model_name=self.queryset.model._meta.label,
            object_id=str(object_id),
            reason=reason,
        )

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
