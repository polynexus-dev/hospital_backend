"""
Shared plumbing for the many "register"-style ViewSets the NABH HIS/EMR
standard asks for (infection registers, CSSD cycles, diet orders, ...).
Every one of them needs exactly the same four things the hand-written
ViewSets elsewhere spell out individually — tenant scoping, field-level
audit on create/update/delete, role-based model permissions, and stamping
`hospital` + "who did this" on create — so this base class spells them out
once instead of 60 more times.

It deliberately adds nothing else: domain rules (state transitions,
alerts, locking) still live on the concrete ViewSet or in a services
module, the same as everywhere else in the codebase.
"""
from rest_framework import serializers, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from .permissions import ActionPermissionRequired, RequiresClinicalDetailPermission, RoleBasedModelPermissions
from .viewsets import AuditedModelViewSetMixin, TenantScopedViewSetMixin

ADMIN_PERMISSION_CLASSES = [IsAuthenticated, RoleBasedModelPermissions, ActionPermissionRequired]
CLINICAL_PERMISSION_CLASSES = ADMIN_PERMISSION_CLASSES + [RequiresClinicalDetailPermission]


class TenantCRUDViewSet(AuditedModelViewSetMixin, TenantScopedViewSetMixin, viewsets.ModelViewSet):
    """`actor_field` names the FK (if any) that should be stamped with the
    requesting user on create — e.g. "reported_by" — so clients can't
    spoof who recorded a clinical or safety event."""

    permission_classes = ADMIN_PERMISSION_CLASSES
    actor_field: str | None = None

    def perform_create(self, serializer):
        hospital = getattr(self.request.user, "hospital", None)
        if hospital is None:
            raise ValidationError("The requesting user is not attached to a hospital.")
        extra = {"hospital": hospital}
        if self.actor_field:
            extra[self.actor_field] = self.request.user
        serializer.save(**extra)
        self._log("create", serializer.instance)


class ClinicalCRUDViewSet(TenantCRUDViewSet):
    permission_classes = CLINICAL_PERMISSION_CLASSES


class TenantModelSerializer(serializers.ModelSerializer):
    """Hides `hospital` and makes the usual bookkeeping fields read-only.
    Also rejects any FK that points at another hospital's row — DRF's
    default PrimaryKeyRelatedField queryset is the *unscoped* manager, so
    without this a client could attach, say, a Patient id from a different
    tenant to their own record."""

    READ_ONLY_DEFAULTS = ("id", "created_at", "updated_at")

    def get_fields(self):
        fields = super().get_fields()
        fields.pop("hospital", None)
        for name in self.READ_ONLY_DEFAULTS:
            if name in fields:
                fields[name].read_only = True
        return fields

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        hospital_id = getattr(getattr(request, "user", None), "hospital_id", None)
        if hospital_id is None:
            return attrs
        for name, value in attrs.items():
            values = value if isinstance(value, (list, tuple)) else [value]
            for v in values:
                other = getattr(v, "hospital_id", None)
                if other is not None and other != hospital_id:
                    raise ValidationError({name: "Refers to a record from a different hospital."})
        return attrs


def model_serializer(model, *, fields="__all__", read_only=(), extra=None):
    """Builds a TenantModelSerializer for `model` on the fly. `extra` maps
    extra declared field names to serializer field instances (e.g. a
    patient_name ReadOnlyField)."""
    extra = extra or {}
    meta_fields = fields
    if fields != "__all__" and extra:
        meta_fields = list(fields) + [k for k in extra if k not in fields]
    meta = type("Meta", (), {"model": model, "fields": meta_fields, "read_only_fields": tuple(read_only)})
    attrs = {"Meta": meta, **extra}
    return type(f"{model.__name__}Serializer", (TenantModelSerializer,), attrs)
