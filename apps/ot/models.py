from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.appointments.models import Doctor
from apps.core.models import FinalizableModel, TenantScopedModel
from apps.ipd.models import Admission
from apps.patients.models import Patient


class SurgeryRequest(TenantScopedModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        APPROVED = "approved", "Approved"
        SCHEDULED = "scheduled", "Scheduled"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="surgery_requests")
    admission = models.ForeignKey(Admission, on_delete=models.SET_NULL, null=True, blank=True, related_name="surgery_requests")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    proposed_procedure = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Surgery Request: {self.proposed_procedure} for {self.patient}"


class OTSchedule(TenantScopedModel):
    surgery_request = models.OneToOneField(SurgeryRequest, on_delete=models.CASCADE, related_name="schedule")
    operation_theatre_room = models.CharField(max_length=120)
    surgeon = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="ot_schedules")
    anaesthetist = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="anaesthesia_schedules")
    scheduled_start = models.DateTimeField()
    scheduled_end = models.DateTimeField()

    class Meta:
        ordering = ["scheduled_start"]

    def __str__(self):
        return f"OT Schedule: {self.surgery_request.proposed_procedure} in {self.operation_theatre_room}"


class PreOpChecklist(TenantScopedModel):
    surgery_request = models.OneToOneField(SurgeryRequest, on_delete=models.CASCADE, related_name="preop_checklist")
    consent_obtained = models.BooleanField(default=False)
    fasting_confirmed = models.BooleanField(default=False)
    site_marked = models.BooleanField(default=False)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"PreOp Checklist for {self.surgery_request}"


class OperativeNote(FinalizableModel, TenantScopedModel):
    FINALIZED_LOCKED_FIELDS = ("procedure_performed", "findings")

    ot_schedule = models.OneToOneField(OTSchedule, on_delete=models.CASCADE, related_name="operative_note")
    procedure_performed = models.TextField()
    findings = models.TextField(blank=True)
    surgeon = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="operative_notes")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField()

    class Meta:
        permissions = [
            ("finalize_operativenote", "Can finalize an operative note"),
        ]

    def __str__(self):
        return f"Operative Note: {self.procedure_performed}"


class AnaesthesiaRecord(FinalizableModel, TenantScopedModel):
    FINALIZED_LOCKED_FIELDS = ("anaesthesia_type", "intra_op_notes")

    ot_schedule = models.OneToOneField(OTSchedule, on_delete=models.CASCADE, related_name="anaesthesia_record")
    anaesthesia_type = models.CharField(max_length=120)
    intra_op_notes = models.TextField(blank=True)
    anaesthetist = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="anaesthesia_records")

    class Meta:
        permissions = [
            ("finalize_anaesthesiarecord", "Can finalize an anaesthesia record"),
        ]

    def __str__(self):
        return f"Anaesthesia Record ({self.anaesthesia_type}) for {self.ot_schedule}"


class ConsumableUsage(TenantScopedModel):
    ot_schedule = models.ForeignKey(OTSchedule, on_delete=models.CASCADE, related_name="consumable_usages")
    item_name = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"{self.item_name} x {self.quantity}"


class ImplantUsage(TenantScopedModel):
    ot_schedule = models.ForeignKey(OTSchedule, on_delete=models.CASCADE, related_name="implant_usages")
    implant_name = models.CharField(max_length=255)
    serial_number = models.CharField(max_length=120, blank=True)
    quantity = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"Implant: {self.implant_name} ({self.serial_number})"
