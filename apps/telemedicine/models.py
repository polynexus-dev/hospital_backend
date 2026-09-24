"""
Telemedicine / video consultation (NABH COP.10.a). Video runs on a Jitsi
Meet server (meet.jit.si by default, or the hospital's own instance via
TELEMEDICINE_JITSI_BASE_URL) — each consult gets an unguessable room.
The patient joins with a signed link (no account needed); consent and
join times are captured for the medical record, per the Telemedicine
Practice Guidelines 2020.
"""
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel


def _room():
    return "hc-" + secrets.token_urlsafe(18).replace("_", "").replace("-", "")[:24]


class TeleConsultation(TenantScopedModel):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        WAITING = "waiting", "Patient waiting"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"
        NO_SHOW = "no_show", "No-show"

    class Mode(models.TextChoices):
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        CHAT = "chat", "Text / chat"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="teleconsultations")
    doctor = models.ForeignKey("appointments.Doctor", on_delete=models.PROTECT, related_name="teleconsultations")
    appointment = models.OneToOneField("appointments.Appointment", on_delete=models.SET_NULL, null=True, blank=True, related_name="teleconsultation")
    scheduled_at = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=15)
    mode = models.CharField(max_length=6, choices=Mode.choices, default=Mode.VIDEO)
    room_name = models.CharField(max_length=40, default=_room, unique=True, editable=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SCHEDULED)
    reason = models.CharField(max_length=255, blank=True)
    is_follow_up = models.BooleanField(default=False)
    consent_given = models.BooleanField(default=False)
    consent_at = models.DateTimeField(null=True, blank=True)
    patient_joined_at = models.DateTimeField(null=True, blank=True)
    doctor_joined_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    clinical_notes = models.TextField(blank=True)
    prescription = models.ForeignKey("patients.Prescription", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    link_sent_at = models.DateTimeField(null=True, blank=True)
    fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-scheduled_at"]

    @property
    def video_url(self):
        base = getattr(settings, "TELEMEDICINE_JITSI_BASE_URL", "https://meet.jit.si").rstrip("/")
        return f"{base}/{self.room_name}"

    @property
    def wait_minutes(self):
        if self.patient_joined_at and self.doctor_joined_at:
            return max(0, round((self.doctor_joined_at - self.patient_joined_at).total_seconds() / 60, 1))
        return None
