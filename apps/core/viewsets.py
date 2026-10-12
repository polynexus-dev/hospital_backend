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

        assigned = {self.assignment_scope_field: user}  # a named dict: Cython can't compile **{...} inline
        scoped_queryset = hospital_queryset.filter(**assigned)
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


AUDIT_SKIPPED_FIELDS = {"id", "hospital_id", "created_at", "updated_at"}
AUDIT_VALUE_LIMIT = 2000


class AuditedModelViewSetMixin:
    """Field-level audit trail (who changed what, old and new value) for a
    ViewSet's creates, updates and deletes.

    Changes are captured where the serializer saves, so it works whichever
    perform_create/perform_update or custom action does the saving. Every
    field is compared unless `audited_fields` narrows it; encrypted fields
    (and `audit_redacted_fields`) are recorded as changed without values,
    so identifiers never reappear in plain text in the audit log."""

    audited_fields: tuple = ()
    # Fields recorded as changed but never by value — encrypted identifiers
    # and contact details must not reappear in plain text in the audit log.
    audit_redacted_fields: tuple = ()

    def _audit_fields(self, instance):
        if self.audited_fields:
            return self.audited_fields
        return tuple(f.attname for f in instance._meta.concrete_fields if f.attname not in AUDIT_SKIPPED_FIELDS)

    def _redacted(self, instance):
        from apps.core.fields import EncryptedCharField, EncryptedJSONField, EncryptedTextField

        encrypted = {
            name
            for f in instance._meta.concrete_fields
            if isinstance(f, (EncryptedCharField, EncryptedTextField, EncryptedJSONField))
            for name in (f.name, f.attname)
        }
        return encrypted | set(self.audit_redacted_fields)

    @staticmethod
    def _value(value):
        text = str(value)
        return text if len(text) <= AUDIT_VALUE_LIMIT else text[:AUDIT_VALUE_LIMIT] + "…"

    def _log(self, action, instance, old=None):
        from apps.core.audit import log_action

        # One entry per record and action per request: explicit _log calls in
        # viewsets don't duplicate what the serializer hook already recorded.
        logged = self.__dict__.setdefault("_audit_logged", set())
        key = (action, type(instance).__name__, getattr(instance, "pk", None))
        if key in logged:
            return
        fields, redacted = self._audit_fields(instance), self._redacted(instance)
        changes = {}
        if action == "update" and old is not None:
            for field in fields:
                old_value, new_value = getattr(old, field, None), getattr(instance, field, None)
                if old_value != new_value:
                    changes[field] = {"changed": True} if field in redacted else {"old": self._value(old_value), "new": self._value(new_value)}
            if not changes:
                return  # saved without changing anything
        elif action == "create":
            changes = {field: "[redacted]" if field in redacted else self._value(getattr(instance, field, None)) for field in fields}
        elif action == "update":
            changes = {"via": getattr(self, "action", "") or "update"}  # a workflow step that saved the record itself
        logged.add(key)
        log_action(actor=self.request.user, action=action, instance=instance, changes=changes, request=self.request)

    def get_serializer(self, *args, **kwargs):
        serializer = super().get_serializer(*args, **kwargs)
        if "data" in kwargs or (len(args) > 1):
            original_save = serializer.save

            def audited_save(**save_kwargs):
                instance = serializer.instance
                old = type(instance)._base_manager.filter(pk=instance.pk).first() if instance is not None and instance.pk else None
                saved = original_save(**save_kwargs)
                self._log("update" if old is not None else "create", saved, old=old)
                return saved

            serializer.save = audited_save
        return serializer

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        # READ capture is opt-in per model via an AuditRule (DAC.2.c) —
        # log_action drops it unless a rule asks for it.
        instance = getattr(self, "_retrieved_instance", None)
        if instance is not None:
            self._log("read", instance)
        return response

    def get_object(self):
        obj = super().get_object()
        if getattr(self, "action", None) == "retrieve":
            self._retrieved_instance = obj
        return obj

    def perform_destroy(self, instance):
        self._log("delete", instance)
        super().perform_destroy(instance)
