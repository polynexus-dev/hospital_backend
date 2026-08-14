import uuid

from django.conf import settings
from django.db import models

from .managers import TenantManager


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Hospital(TimeStampedModel):
    """The tenant. One row per hospital, whether served from shared SaaS
    infrastructure or a single-hospital on-premise install."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    city = models.CharField(max_length=120, blank=True)
    state = models.CharField(max_length=120, blank=True)
    address = models.TextField(blank=True)
    timezone = models.CharField(max_length=64, default="Asia/Kolkata")
    primary_language = models.CharField(max_length=8, default="mr")
    is_on_premise = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    google_review_url = models.URLField(blank=True, help_text="Where NPS promoters get routed (§10).")
    owner_mis_whatsapp_number = models.CharField(max_length=20, blank=True, help_text="Where the daily WhatsApp MIS is sent (§12).")
    lead_webhook_token = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False,
        help_text="Secret used in the inbound lead-capture webhook URL (website forms, Meta/Google lead ads). Regenerate to revoke.",
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
