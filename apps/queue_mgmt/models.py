"""
Queue & token management (NABH AAC.2.h/i): service points for every
counter (registration, billing, lab collection, pharmacy, radiology,
doctor consultation), daily token sequences, call-next / serve / skip,
estimated waiting time, and a public display board per screen.

Doctor consultation queues already exist on Appointment.queue_token
(apps.appointments.services.check_in / doctor_queue); a SERVICE_POINT of
kind "consultation" linked to a doctor surfaces that queue on the same
display rather than duplicating it.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel


class ServicePoint(TenantScopedModel):
    class Kind(models.TextChoices):
        REGISTRATION = "registration", "Registration"
        CONSULTATION = "consultation", "Doctor consultation"
        BILLING = "billing", "Billing / cash"
        LAB = "lab", "Sample collection"
        RADIOLOGY = "radiology", "Radiology"
        PHARMACY = "pharmacy", "Pharmacy"
        VACCINATION = "vaccination", "Vaccination"
        OTHER = "other", "Other"

    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    counter_label = models.CharField(max_length=40, blank=True, help_text="Shown on the display, e.g. 'Counter 3' or 'Room 12'.")
    token_prefix = models.CharField(max_length=4, default="A")
    doctor = models.ForeignKey("appointments.Doctor", on_delete=models.SET_NULL, null=True, blank=True, related_name="service_points")
    default_service_minutes = models.PositiveSmallIntegerField(default=5)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["kind", "name"]

    def __str__(self):
        return self.name


class QueueDisplay(TenantScopedModel):
    """A TV / kiosk screen. The public board URL carries `key`, not a login."""

    name = models.CharField(max_length=100)
    key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    service_points = models.ManyToManyField(ServicePoint, related_name="displays")
    announcement = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.name


class QueueToken(TenantScopedModel):
    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting"
        CALLED = "called", "Called"
        SERVING = "serving", "Being served"
        DONE = "done", "Done"
        SKIPPED = "skipped", "Skipped / no response"
        CANCELLED = "cancelled", "Cancelled"

    service_point = models.ForeignKey(ServicePoint, on_delete=models.CASCADE, related_name="tokens")
    token_date = models.DateField(default=timezone.localdate)
    number = models.PositiveIntegerField()
    patient = models.ForeignKey("patients.Patient", on_delete=models.SET_NULL, null=True, blank=True, related_name="queue_tokens")
    appointment = models.ForeignKey("appointments.Appointment", on_delete=models.SET_NULL, null=True, blank=True, related_name="queue_tokens")
    visitor_name = models.CharField(max_length=150, blank=True)
    priority = models.BooleanField(default=False, help_text="Senior citizen / emergency / differently-abled — served first.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.WAITING)
    issued_at = models.DateTimeField(default=timezone.now)
    called_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    served_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    recall_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-priority", "number"]
        constraints = [models.UniqueConstraint(fields=["service_point", "token_date", "number"], name="unique_token_per_point_per_day")]

    @property
    def label(self):
        return f"{self.service_point.token_prefix}{self.number:03d}"
