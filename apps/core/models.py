import re
import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils import timezone as tz

from .managers import TenantManager

RESERVED_HOSPITAL_SLUGS = {
    "admin", "api", "app", "dashboard", "login", "logout",
    "register", "signup", "static", "media", "health", "metrics",
}


def validate_hospital_slug(value: str) -> None:
    if not re.match(r"^[a-z0-9-]+$", value):
        raise ValidationError("Slug can only contain lowercase letters, numbers, and hyphens.")
    if value.lower() in RESERVED_HOSPITAL_SLUGS:
        raise ValidationError(f"'{value}' is a reserved system slug and cannot be used for a hospital subdomain.")


ALL_MODULES = [
    "opd", "ipd", "nursing", "laboratory", "radiology", "pharmacy",
    "emergency", "ot", "icu", "bloodbank", "finance", "hr", "billing", "inventory"
]
ALL_MODULE_KEYS = ALL_MODULES



def default_enabled_modules():
    return ALL_MODULES


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class HospitalGroup(TimeStampedModel):
    """Optional multi-hospital group / chain entity."""
    name = models.CharField(max_length=255)
    owner_org_name = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Hospital(TimeStampedModel):
    """The tenant. One row per hospital, whether served from shared SaaS
    infrastructure or a single-hospital on-premise install."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(HospitalGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="hospitals")
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, validators=[validate_hospital_slug])
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120, blank=True)
    address = models.TextField(blank=True)
    timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    primary_language = models.CharField(max_length=8, default="mr")
    is_on_premise = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    enabled_modules = models.JSONField(
        default=default_enabled_modules,
        blank=True,
        help_text="Modules enabled for this hospital SaaS subscription.",
    )
    google_review_url = models.URLField(blank=True, help_text="Where NPS promoters get routed (§10).")
    owner_mis_whatsapp_number = models.CharField(max_length=20, blank=True, help_text="Where the daily WhatsApp MIS is sent (§12).")
    lead_webhook_token = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False,
        help_text="Secret used in the inbound lead-capture webhook URL (website forms, Meta/Google lead ads). Regenerate to revoke.",
    )
    next_uhid_sequence = models.PositiveIntegerField(
        default=1, editable=False,
        help_text="Next UHID sequence number for this hospital — see apps.patients.Patient._generate_uhid.",
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Department(TimeStampedModel):
    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="departments")
    name = models.CharField(max_length=120)
    code = models.CharField(max_length=32, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    objects = TenantManager()

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["hospital", "name"], name="unique_department_per_hospital"),
        ]

    def __str__(self):
        return f"{self.name} ({self.hospital.name})"


class TenantScopedModel(TimeStampedModel):
    """Base class for every domain model that belongs to a single hospital."""

    hospital = models.ForeignKey(Hospital, on_delete=models.CASCADE, related_name="+")

    objects = TenantManager()

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    """Soft-delete with actor/reason/timestamp (Part A #1) — patient-
    identifying and clinical records are never hard-deleted through normal
    application code. Combine with apps.core.managers.SoftDeleteManager /
    SoftDeleteQuerySet so the default manager excludes deleted rows
    (see apps.patients.models.Patient for the pattern).

    `.delete()` soft-deletes; `.hard_delete()` is the real thing, reserved
    for the retention/purge job (apps.automation.tasks) once a record's
    retention period has actually expired."""

    is_deleted = models.BooleanField(default=False, editable=False)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        editable=False, related_name="+",
    )
    delete_reason = models.CharField(max_length=255, blank=True, editable=False)

    class Meta:
        abstract = True

    def delete(self, *args, actor=None, reason="", **kwargs):
        self.is_deleted = True
        self.deleted_at = tz.now()
        self.deleted_by = actor
        self.delete_reason = reason
        self.save(update_fields=["is_deleted", "deleted_at", "deleted_by", "delete_reason"])

    def hard_delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)


class AuditLog(models.Model):
    """
    Append-only audit trail. Records are never updated or deleted by
    application code — see AuditMiddleware and apps.core.audit.log_action.
    """

    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"
        READ = "read", "Read"
        REQUEST = "request", "Request"

    hospital = models.ForeignKey(Hospital, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs")
    action = models.CharField(max_length=16, choices=Action.choices)
    model_name = models.CharField(max_length=100, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    method = models.CharField(max_length=8, blank=True)
    path = models.CharField(max_length=512, blank=True)
    status_code = models.PositiveIntegerField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["hospital", "created_at"]),
            models.Index(fields=["model_name", "object_id"]),
        ]

    def __str__(self):
        return f"{self.action} {self.model_name}:{self.object_id} by {self.actor_id}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("AuditLog records are immutable and cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AuditLog records are immutable and cannot be deleted.")


class FinalizableModel(models.Model):
    """Mixin for clinical/financial records that must not be silently
    edited once finalized."""

    FINALIZED_LOCKED_FIELDS: tuple = ()

    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk and self.finalized_at and self.FINALIZED_LOCKED_FIELDS:
            original = type(self)._base_manager.filter(pk=self.pk).values(*self.FINALIZED_LOCKED_FIELDS).first()
            if original is not None:
                changed = [f for f in self.FINALIZED_LOCKED_FIELDS if getattr(self, f) != original[f]]
                if changed:
                    raise ValueError(
                        f"{type(self).__name__}.{', '.join(changed)} is finalized and cannot be edited "
                        f"directly — create an Amendment instead."
                    )
        super().save(*args, **kwargs)

    def finalize(self, user):
        self.finalized_at = timezone.now()
        self.finalized_by = user
        self.save(update_fields=["finalized_at", "finalized_by"])


class Amendment(TenantScopedModel):
    """A correction to an already-finalized FinalizableModel record."""

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField(max_length=64)
    content_object = GenericForeignKey("content_type", "object_id")

    field_name = models.CharField(max_length=100)
    previous_value = models.TextField(blank=True)
    corrected_value = models.TextField(blank=True)
    reason = models.TextField()
    amended_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    amended_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-amended_at"]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
        ]

    def __str__(self):
        return f"Amendment to {self.content_type.model}:{self.object_id}.{self.field_name}"
