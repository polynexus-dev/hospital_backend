from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.appointments.models import Doctor
from apps.core.models import FinalizableModel, TenantScopedModel
from apps.facilities.models import Bed
from apps.ipd.models import Admission


class ICUAdmission(TenantScopedModel):
    admission = models.OneToOneField(Admission, on_delete=models.CASCADE, related_name="icu_admission")
    bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="icu_admissions")
    ventilator_required = models.BooleanField(default=False)
    admitted_at = models.DateTimeField(default=timezone.now)
    discharged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-admitted_at"]

    def __str__(self):
        return f"ICU Admission for {self.admission.patient} (Bed: {self.bed.bed_number})"


class VentilatorLog(TenantScopedModel):
    icu_admission = models.ForeignKey(ICUAdmission, on_delete=models.CASCADE, related_name="ventilator_logs")
    mode = models.CharField(max_length=64)
    ventilator_settings = models.JSONField(default=dict, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-recorded_at"]

    def __str__(self):
        return f"Ventilator Log ({self.mode}) - {self.icu_admission}"


class ICUDailyProgressNote(FinalizableModel, TenantScopedModel):
    FINALIZED_LOCKED_FIELDS = ("note",)

    icu_admission = models.ForeignKey(ICUAdmission, on_delete=models.CASCADE, related_name="progress_notes")
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="icu_progress_notes")
    note = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("finalize_icudailyprogressnote", "Can finalize an ICU daily progress note"),
        ]

    def __str__(self):
        return f"ICU Note by Dr. {self.doctor} for {self.icu_admission}"
