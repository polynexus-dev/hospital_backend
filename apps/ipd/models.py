from django.conf import settings
from django.db import models

from apps.appointments.models import Doctor
from apps.core.models import Department, FinalizableModel, TenantScopedModel
from apps.facilities.models import Bed
from apps.opd.models import Encounter
from apps.patients.models import Patient


class Admission(TenantScopedModel):
    class AdmissionType(models.TextChoices):
        PLANNED = "planned", "Planned"
        EMERGENCY = "emergency", "Emergency"

    class Status(models.TextChoices):
        ADMITTED = "admitted", "Admitted"
        DISCHARGED = "discharged", "Discharged"
        DAMA = "dama", "Discharge Against Medical Advice"
        DECEASED = "deceased", "Deceased"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="admissions")
    admitting_doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="admissions")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="admissions")
    bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="admissions")
    # Optional — not every admission originates from an OPD visit (planned
    # surgery admissions, or the emergency path Phase 6 adds, have none).
    # See docs/erp/05-integration-architecture.md for why this is a plain
    # nullable back-reference rather than an event-triggered creation: OPD's
    # Appointment state machine has no "admission needed" status to hang a
    # signal off, and forcing one in just for this would be the wrong fix.
    source_encounter = models.ForeignKey(Encounter, on_delete=models.SET_NULL, null=True, blank=True, related_name="resulting_admissions")
    source_ed_visit = models.ForeignKey("emergency.EDVisit", on_delete=models.SET_NULL, null=True, blank=True, related_name="resulting_admissions")

    admission_type = models.CharField(max_length=16, choices=AdmissionType.choices, default=AdmissionType.PLANNED)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ADMITTED)
    admission_diagnosis = models.TextField(blank=True)

    admitted_at = models.DateTimeField(auto_now_add=True)
    discharged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-admitted_at"]
        indexes = [models.Index(fields=["hospital", "status"])]

    def __str__(self):
        return f"Admission: {self.patient} ({self.get_status_display()})"


class BedAllocation(TenantScopedModel):
    """History trail — Admission.bed is the current bed; this is every bed
    an admission has occupied, with released_at=None marking the current
    one. Populated exclusively by apps.ipd.services, never edited directly
    — same discipline as facilities.Bed.status/current_admission."""

    admission = models.ForeignKey(Admission, on_delete=models.CASCADE, related_name="bed_allocations")
    bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="allocations")
    allocated_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-allocated_at"]

    def __str__(self):
        return f"{self.bed} for {self.admission}"


class WardTransfer(TenantScopedModel):
    admission = models.ForeignKey(Admission, on_delete=models.CASCADE, related_name="ward_transfers")
    from_bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="transfers_from")
    to_bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="transfers_to")
    reason = models.TextField(blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    requested_at = models.DateTimeField(auto_now_add=True)
    transferred_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"Transfer {self.admission}: {self.from_bed} -> {self.to_bed}"


class DoctorProgressNote(TenantScopedModel, FinalizableModel):
    FINALIZED_LOCKED_FIELDS = ("note",)

    admission = models.ForeignKey(Admission, on_delete=models.CASCADE, related_name="progress_notes")
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="ipd_progress_notes")
    note = models.TextField()

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("finalize_doctorprogressnote", "Can finalize a doctor progress note"),
        ]

    def __str__(self):
        return f"Progress note for {self.admission}"


class DischargeSummary(TenantScopedModel, FinalizableModel):
    FINALIZED_LOCKED_FIELDS = (
        "final_diagnosis", "procedures_performed", "treatment_summary",
        "discharge_medications", "follow_up_instructions", "discharge_type",
    )

    class DischargeType(models.TextChoices):
        ROUTINE = "routine", "Routine"
        DAMA = "dama", "Discharge Against Medical Advice"
        REFERRED = "referred", "Referred"
        DECEASED = "deceased", "Deceased"

    admission = models.OneToOneField(Admission, on_delete=models.CASCADE, related_name="discharge_summary")
    final_diagnosis = models.TextField(blank=True)
    procedures_performed = models.TextField(blank=True)
    treatment_summary = models.TextField(blank=True)
    discharge_medications = models.TextField(blank=True)
    follow_up_instructions = models.TextField(blank=True)
    discharge_type = models.CharField(max_length=16, choices=DischargeType.choices, default=DischargeType.ROUTINE)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        permissions = [
            ("finalize_dischargesummary", "Can finalize a discharge summary"),
        ]

    def __str__(self):
        return f"Discharge summary for {self.admission}"
