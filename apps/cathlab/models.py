"""Cardiac catheterisation lab — procedures, findings, devices, radiation &
contrast safety, and door-to-balloon time for primary PCI."""
from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import TenantScopedModel

D2B_TARGET_MINUTES = 90
# SIR/ACR: peak skin dose follow-up is advised above ~5 Gy reference air kerma.
AIR_KERMA_FOLLOW_UP_MGY = 5000


class CathProcedure(TenantScopedModel):
    class Type(models.TextChoices):
        CAG = "cag", "Coronary angiography"
        PTCA = "ptca", "PTCA / elective PCI"
        PRIMARY_PCI = "primary_pci", "Primary PCI (STEMI)"
        PACEMAKER = "ppi", "Permanent pacemaker"
        ICD_CRT = "icd_crt", "ICD / CRT"
        EP_STUDY = "ep_study", "EP study / ablation"
        STRUCTURAL = "structural", "Structural (TAVI / BMV / device closure)"
        PERIPHERAL = "peripheral", "Peripheral angiography / plasty"
        OTHER = "other", "Other"

    class Urgency(models.TextChoices):
        ELECTIVE = "elective", "Elective"
        URGENT = "urgent", "Urgent"
        EMERGENCY = "emergency", "Emergency / STEMI"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETED = "completed", "Completed"
        ABANDONED = "abandoned", "Abandoned"

    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="cath_procedures")
    admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="cath_procedures")
    ed_visit = models.ForeignKey("emergency.EDVisit", on_delete=models.SET_NULL, null=True, blank=True, related_name="cath_procedures")
    procedure_type = models.CharField(max_length=12, choices=Type.choices)
    urgency = models.CharField(max_length=10, choices=Urgency.choices, default=Urgency.ELECTIVE)
    indication = models.CharField(max_length=255)
    operator = models.ForeignKey("appointments.Doctor", on_delete=models.PROTECT, related_name="cath_procedures")
    assistant = models.ForeignKey("appointments.Doctor", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    procedure_tariff = models.ForeignKey("finance.ServiceTariff", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SCHEDULED)
    scheduled_at = models.DateTimeField(null=True, blank=True)

    # Timings — door is the hospital arrival (taken from the ED visit when linked).
    door_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True, help_text="Sheath in")
    device_at = models.DateTimeField(null=True, blank=True, help_text="First balloon / device (reperfusion)")
    ended_at = models.DateTimeField(null=True, blank=True, help_text="Sheath out")
    delay_reason = models.CharField(max_length=255, blank=True, help_text="Required when a primary PCI misses the door-to-device target")

    # Access, contrast & radiation.
    access_site = models.CharField(max_length=20, blank=True, help_text="right radial, left radial, right femoral, …")
    sheath_fr = models.PositiveSmallIntegerField(null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    creatinine_mg_dl = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    contrast_agent = models.CharField(max_length=60, blank=True)
    contrast_ml = models.PositiveIntegerField(null=True, blank=True)
    fluoro_minutes = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    air_kerma_mgy = models.PositiveIntegerField(null=True, blank=True)
    dap_gy_cm2 = models.DecimalField(max_digits=8, decimal_places=1, null=True, blank=True)
    heparin_units = models.PositiveIntegerField(null=True, blank=True)

    # Findings.
    dominance = models.CharField(max_length=10, blank=True, help_text="right / left / co-dominant")
    lv_ef_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    vessel_findings = models.JSONField(default=list, blank=True, help_text='[{"vessel": "LAD", "segment": "proximal", "stenosis_percent": 90, "timi_flow": 2, "intervention": "DES"}]')
    complications = models.JSONField(default=list, blank=True)
    conclusion = models.TextField(blank=True)
    recommendation = models.CharField(max_length=20, blank=True, help_text="medical / pci / cabg / surgery / other")
    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-scheduled_at", "-created_at"]
        indexes = [models.Index(fields=["hospital", "procedure_type", "status"])]

    @property
    def door_to_device_minutes(self):
        if self.door_at and self.device_at:
            return round((self.device_at - self.door_at).total_seconds() / 60)
        return None

    @property
    def max_contrast_ml(self):
        """Cigarroa maximum allowable contrast dose: 5 mL × weight (kg) / creatinine (mg/dL), capped at 300 mL."""
        if self.weight_kg and self.creatinine_mg_dl:
            return int(min(Decimal("5") * self.weight_kg / self.creatinine_mg_dl, Decimal("300")))
        return None

    @property
    def safety_flags(self):
        flags = []
        if self.max_contrast_ml and self.contrast_ml and self.contrast_ml > self.max_contrast_ml:
            flags.append(f"Contrast {self.contrast_ml} mL exceeds the maximum allowable {self.max_contrast_ml} mL — watch for contrast nephropathy.")
        if self.air_kerma_mgy and self.air_kerma_mgy >= AIR_KERMA_FOLLOW_UP_MGY:
            flags.append(f"Air kerma {self.air_kerma_mgy} mGy — arrange skin-dose follow-up at 2–4 weeks.")
        d2b = self.door_to_device_minutes
        if self.procedure_type == self.Type.PRIMARY_PCI and d2b is not None and d2b > D2B_TARGET_MINUTES:
            flags.append(f"Door-to-device {d2b} min exceeds the {D2B_TARGET_MINUTES}-min target — record the reason for delay.")
        return flags


class CathDevice(TenantScopedModel):
    """Stents, balloons, pacemakers, leads, occluders, valves — with traceability."""

    class Kind(models.TextChoices):
        DES = "des", "Drug-eluting stent"
        BMS = "bms", "Bare-metal stent"
        BALLOON = "balloon", "Balloon"
        DCB = "dcb", "Drug-coated balloon"
        PACEMAKER = "pacemaker", "Pacemaker / ICD generator"
        LEAD = "lead", "Lead"
        OCCLUDER = "occluder", "Occluder"
        VALVE = "valve", "Valve"
        OTHER = "other", "Other"

    procedure = models.ForeignKey(CathProcedure, on_delete=models.CASCADE, related_name="devices")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    brand = models.CharField(max_length=100)
    model_name = models.CharField(max_length=100, blank=True)
    size = models.CharField(max_length=40, blank=True, help_text="e.g. 3.0 × 28 mm")
    lot_number = models.CharField(max_length=60)
    udi = models.CharField(max_length=120, blank=True)
    serial_number = models.CharField(max_length=60, blank=True)
    vessel = models.CharField(max_length=30, blank=True)
    quantity = models.PositiveSmallIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="Billed price (NPPA ceiling for coronary stents)")

    class Meta:
        ordering = ["id"]
