"""
Hospital infection prevention & control (NABH HIS/EMR COP.8.a/b/d and the
HAI rates in the PSQ.3 KPI set: CAUTI, VAP, CLABSI per 1000 device-days,
SSI %, hand-hygiene compliance, needlestick injuries).
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel

USER = settings.AUTH_USER_MODEL


class DeviceEpisode(TenantScopedModel):
    """Insertion→removal of an invasive device. Device-days (the HAI rate
    denominators) are derived from these, per NABH/CDC-NHSN definitions."""

    class Device(models.TextChoices):
        URINARY_CATHETER = "urinary_catheter", "Indwelling urinary catheter"
        CENTRAL_LINE = "central_line", "Central line"
        VENTILATOR = "ventilator", "Ventilator"
        PERIPHERAL_IV = "peripheral_iv", "Peripheral IV"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="device_episodes")
    admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="device_episodes")
    device = models.CharField(max_length=20, choices=Device.choices)
    site = models.CharField(max_length=80, blank=True)
    inserted_at = models.DateTimeField(default=timezone.now)
    removed_at = models.DateTimeField(null=True, blank=True)
    inserted_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    bundle_compliance = models.JSONField(default=dict, blank=True, help_text="Insertion/maintenance bundle checklist answers.")

    class Meta:
        ordering = ["-inserted_at"]

    def device_days_between(self, start, end):
        s = max(self.inserted_at, start)
        e = min(self.removed_at or timezone.now(), end)
        if e <= s:
            return 0
        return max(1, round((e - s).total_seconds() / 86400))


class HAIIncident(TenantScopedModel):
    """COP.8.a — each infection tracked as its own case."""

    class InfectionType(models.TextChoices):
        CAUTI = "cauti", "CAUTI"
        CLABSI = "clabsi", "CLABSI"
        VAP = "vap", "VAP"
        SSI = "ssi", "Surgical site infection"
        MRSA = "mrsa", "MRSA / MDRO colonisation"
        CDI = "cdi", "C. difficile"
        GASTRO = "gastroenteritis", "Gastroenteritis"
        OTHER = "other", "Other HAI"

    class Status(models.TextChoices):
        SUSPECTED = "suspected", "Suspected"
        CONFIRMED = "confirmed", "Confirmed"
        RULED_OUT = "ruled_out", "Ruled out"
        RESOLVED = "resolved", "Resolved"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="hai_incidents")
    admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="hai_incidents")
    device_episode = models.ForeignKey(DeviceEpisode, on_delete=models.SET_NULL, null=True, blank=True, related_name="infections")
    surgery = models.ForeignKey("ot.SurgeryRequest", on_delete=models.SET_NULL, null=True, blank=True, related_name="infections")
    infection_type = models.CharField(max_length=16, choices=InfectionType.choices)
    ward = models.CharField(max_length=80, blank=True)
    onset_date = models.DateField(default=timezone.localdate)
    organism = models.CharField(max_length=150, blank=True)
    sensitivity_pattern = models.TextField(blank=True)
    is_mdro = models.BooleanField(default=False)
    isolation_required = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SUSPECTED)
    actions_taken = models.TextField(blank=True)
    reported_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-onset_date"]


class AntimicrobialPolicy(TenantScopedModel):
    """COP.8.b — the hospital's policy, displayed to prescribers. Sections:
    [{title, body}] covering indication, selection, dosing, route,
    duration, timing (the six areas NABH's test case checks for)."""

    title = models.CharField(max_length=200, default="Antimicrobial Usage Policy")
    version = models.CharField(max_length=20, default="1.0")
    effective_from = models.DateField(default=timezone.localdate)
    sections = models.JSONField(default=list)
    document = models.FileField(upload_to="policies/", blank=True)
    is_current = models.BooleanField(default=True)

    class Meta:
        ordering = ["-effective_from"]
        verbose_name_plural = "antimicrobial policies"


class AntimicrobialApproval(TenantScopedModel):
    """Restricted antimicrobials need an approval before dispensing."""

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="antimicrobial_approvals")
    prescription = models.ForeignKey("patients.Prescription", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    drug = models.CharField(max_length=150)
    indication = models.TextField()
    culture_sent = models.BooleanField(default=False)
    culture_report = models.TextField(blank=True)
    planned_duration_days = models.PositiveSmallIntegerField(null=True, blank=True)
    requested_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name="+")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED)
    decided_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]


class StaffExposure(TenantScopedModel):
    """COP.8.d — occupational exposure register with PEP and follow-up."""

    class ExposureType(models.TextChoices):
        NEEDLESTICK = "needlestick", "Needlestick / sharps injury"
        SPLASH = "splash", "Blood/body-fluid splash"
        AIRBORNE = "airborne", "Airborne (TB etc.)"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPEN = "open", "Under follow-up"
        CLOSED = "closed", "Closed — no seroconversion"
        SEROCONVERTED = "seroconverted", "Seroconverted"

    staff = models.ForeignKey(USER, on_delete=models.PROTECT, related_name="exposures")
    exposure_type = models.CharField(max_length=12, choices=ExposureType.choices)
    occurred_at = models.DateTimeField(default=timezone.now)
    location = models.CharField(max_length=100, blank=True)
    source_patient = models.ForeignKey("patients.Patient", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    source_status = models.JSONField(default=dict, blank=True, help_text='{"hiv": "neg", "hbsag": "pos", "hcv": "unknown"}')
    staff_hbv_vaccinated = models.BooleanField(default=False)
    first_aid_given = models.BooleanField(default=True)
    pep_given = models.BooleanField(default=False)
    pep_details = models.TextField(blank=True)
    pep_started_at = models.DateTimeField(null=True, blank=True)
    followups = models.JSONField(default=list, blank=True, help_text="[{due: date, done: bool, result: str}] — 6 wk / 3 mo / 6 mo tests.")
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.OPEN)
    description = models.TextField(blank=True)
    reported_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-occurred_at"]


class HandHygieneAudit(TenantScopedModel):
    """WHO 5-moments observation audit (KPI: hand-hygiene compliance)."""

    audit_date = models.DateField(default=timezone.localdate)
    ward = models.CharField(max_length=80)
    staff_category = models.CharField(max_length=40, blank=True, help_text="doctor / nurse / technician / housekeeping")
    opportunities = models.PositiveIntegerField()
    compliant = models.PositiveIntegerField()
    auditor = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-audit_date"]
