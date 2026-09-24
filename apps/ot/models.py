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
    # NABH KPI "unplanned return to OT" — a re-operation for a complication
    # of an earlier surgery during the same admission.
    is_unplanned_return = models.BooleanField(default=False)
    previous_surgery = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="returns")
    priority = models.CharField(max_length=12, default="elective", help_text="elective | urgent | emergency")
    procedure_type = models.CharField(max_length=20, default="surgery", help_text="surgery | interventional | procedural_sedation (COP.6)")

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

    # COP.6.d schedule / re-schedule / cancel, COP.6.e actual times.
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SCHEDULED)
    reschedule_count = models.PositiveSmallIntegerField(default=0, editable=False)
    last_reschedule_reason = models.CharField(max_length=255, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.CharField(max_length=255, blank=True)
    actual_start = models.DateTimeField(null=True, blank=True)
    actual_end = models.DateTimeField(null=True, blank=True)
    wheeled_in_at = models.DateTimeField(null=True, blank=True)
    wheeled_out_at = models.DateTimeField(null=True, blank=True)

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

    # COP.6.b pre-operative assessment & patient preparation.
    asa_grade = models.CharField(max_length=4, blank=True, help_text="I–VI (E for emergency)")
    airway_assessment = models.CharField(max_length=100, blank=True, help_text="e.g. Mallampati II")
    comorbidities = models.TextField(blank=True)
    investigations_reviewed = models.BooleanField(default=False)
    blood_arranged = models.BooleanField(default=False)
    npo_since = models.DateTimeField(null=True, blank=True)
    premedication = models.TextField(blank=True)
    fit_for_surgery = models.BooleanField(null=True)
    assessment_notes = models.TextField(blank=True)

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

    # COP.6.f — anaesthesia / procedural sedation details.
    asa_grade = models.CharField(max_length=4, blank=True)
    airway_device = models.CharField(max_length=60, blank=True, help_text="ETT size, LMA, face mask, none")
    induction_at = models.DateTimeField(null=True, blank=True)
    reversal_at = models.DateTimeField(null=True, blank=True)
    drugs = models.JSONField(default=list, blank=True, help_text="[{drug, dose, route, time}]")
    vitals = models.JSONField(default=list, blank=True, help_text="[{time, hr, bp, spo2, etco2}]")
    fluids = models.JSONField(default=list, blank=True, help_text="[{fluid, volume_ml}]")
    estimated_blood_loss_ml = models.PositiveIntegerField(null=True, blank=True)
    urine_output_ml = models.PositiveIntegerField(null=True, blank=True)
    complications = models.TextField(blank=True)
    recovery_aldrete_score = models.PositiveSmallIntegerField(null=True, blank=True)
    shifted_to = models.CharField(max_length=60, blank=True, help_text="Recovery / ICU / ward")

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
    # MOM.3.c implant register — traceable for recalls.
    manufacturer = models.CharField(max_length=150, blank=True)
    batch_number = models.CharField(max_length=80, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    implanted_site = models.CharField(max_length=120, blank=True)
    sticker_image = models.ImageField(upload_to="implants/", blank=True)

    def __str__(self):
        return f"Implant: {self.implant_name} ({self.serial_number})"


class SurgicalSafetyChecklist(TenantScopedModel):
    """COP.6.a — WHO Surgical Safety Checklist (also used for procedures in
    wards/OPD via `setting`). Each phase is a dict of item -> bool, stamped
    with who completed it and when."""

    SIGN_IN_ITEMS = ["identity_confirmed", "site_marked", "consent_signed", "anaesthesia_check_done", "pulse_oximeter_on", "allergy_known", "difficult_airway_risk_assessed", "blood_loss_risk_assessed"]
    TIME_OUT_ITEMS = ["team_introduced", "patient_name_procedure_site_confirmed", "antibiotic_prophylaxis_given", "critical_events_reviewed", "imaging_displayed", "sterility_confirmed"]
    SIGN_OUT_ITEMS = ["procedure_recorded", "counts_correct", "specimen_labelled", "equipment_problems_addressed", "recovery_concerns_reviewed"]
    PHASE_ITEMS = {"sign_in": SIGN_IN_ITEMS, "time_out": TIME_OUT_ITEMS, "sign_out": SIGN_OUT_ITEMS}

    ot_schedule = models.OneToOneField(OTSchedule, on_delete=models.CASCADE, null=True, blank=True, related_name="safety_checklist")
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="surgical_safety_checklists")
    setting = models.CharField(max_length=10, default="ot", help_text="ot | ward | opd | cathlab | endoscopy")
    procedure_name = models.CharField(max_length=255)
    sign_in = models.JSONField(default=dict, blank=True)
    sign_in_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    sign_in_at = models.DateTimeField(null=True, blank=True)
    time_out = models.JSONField(default=dict, blank=True)
    time_out_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    time_out_at = models.DateTimeField(null=True, blank=True)
    sign_out = models.JSONField(default=dict, blank=True)
    sign_out_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    sign_out_at = models.DateTimeField(null=True, blank=True)
    antibiotic_prophylaxis_indicated = models.BooleanField(default=True)
    antibiotic_given_at = models.DateTimeField(null=True, blank=True)
    antibiotic_given_within_60_min = models.BooleanField(null=True, editable=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        incision = self.ot_schedule.actual_start if self.ot_schedule_id and self.ot_schedule.actual_start else None
        if self.antibiotic_given_at and incision:
            delta = (incision - self.antibiotic_given_at).total_seconds() / 60
            self.antibiotic_given_within_60_min = 0 <= delta <= 60
        super().save(*args, **kwargs)

    @property
    def is_complete(self):
        return all(all(getattr(self, phase).get(i) for i in items) for phase, items in self.PHASE_ITEMS.items())
