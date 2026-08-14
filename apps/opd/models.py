from django.conf import settings
from django.db import models

from apps.appointments.models import Appointment, Doctor
from apps.core.models import Department, FinalizableModel, TenantScopedModel
from apps.patients.models import Patient


class Encounter(TenantScopedModel):
    """The clinical-content container for one OPD visit. Deliberately does
    NOT duplicate Appointment's own status/queue_token/checked_in_at —
    Appointment already owns the full visit state machine (booked ->
    checked_in -> in_consult -> completed, see apps.appointments.models);
    Encounter exists only to hold what Appointment has no room for
    (vitals, clinical notes, diagnoses, investigation orders). One
    Encounter per Appointment, created automatically at check-in — see
    apps.opd.signals."""

    appointment = models.OneToOneField(Appointment, on_delete=models.CASCADE, related_name="encounter")
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="encounters")
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="encounters")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="encounters")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Encounter: {self.patient} with {self.doctor}"


class VitalsReading(TenantScopedModel):
    encounter = models.ForeignKey(Encounter, on_delete=models.CASCADE, related_name="vitals_readings")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    height_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    bp_systolic = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic = models.PositiveSmallIntegerField(null=True, blank=True)
    pulse = models.PositiveSmallIntegerField(null=True, blank=True)
    temperature_c = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    spo2 = models.PositiveSmallIntegerField(null=True, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-recorded_at"]

    def __str__(self):
        return f"Vitals for {self.encounter} @ {self.recorded_at}"


class ClinicalNote(TenantScopedModel, FinalizableModel):
    """Finalize-locked once the doctor is done — see
    docs/erp/07-audit-and-security.md §2b. Corrections after that point go
    through apps.core.models.Amendment, not a silent edit."""

    FINALIZED_LOCKED_FIELDS = ("chief_complaints", "history", "examination_findings")

    encounter = models.OneToOneField(Encounter, on_delete=models.CASCADE, related_name="clinical_note")
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="clinical_notes")
    chief_complaints = models.TextField(blank=True)
    history = models.TextField(blank=True)
    examination_findings = models.TextField(blank=True)

    class Meta:
        permissions = [
            ("finalize_clinicalnote", "Can finalize a clinical note"),
        ]

    def __str__(self):
        return f"Clinical note for {self.encounter}"


class Diagnosis(TenantScopedModel, FinalizableModel):
    FINALIZED_LOCKED_FIELDS = ("icd_code", "description", "diagnosis_type")

    class DiagnosisType(models.TextChoices):
        PROVISIONAL = "provisional", "Provisional"
        FINAL = "final", "Final"

    encounter = models.ForeignKey(Encounter, on_delete=models.CASCADE, related_name="diagnoses")
    icd_code = models.CharField(max_length=16, blank=True)
    description = models.CharField(max_length=255)
    diagnosis_type = models.CharField(max_length=16, choices=DiagnosisType.choices, default=DiagnosisType.PROVISIONAL)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("finalize_diagnosis", "Can finalize a diagnosis"),
        ]

    def __str__(self):
        return f"{self.description} ({self.get_diagnosis_type_display()})"


class InvestigationOrder(TenantScopedModel):
    """Lightweight placeholder until apps.laboratory/apps.radiology ship in
    Phase 5 with real test/procedure catalogues — `description` is
    freeform for now, same precedent as Prescription.lab_orders."""

    class OrderType(models.TextChoices):
        LAB = "lab", "Lab"
        RADIOLOGY = "radiology", "Radiology"

    class Status(models.TextChoices):
        ORDERED = "ordered", "Ordered"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"

    encounter = models.ForeignKey(Encounter, on_delete=models.CASCADE, related_name="investigation_orders")
    order_type = models.CharField(max_length=16, choices=OrderType.choices)
    description = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ORDERED)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_order_type_display()} order for {self.encounter}"
