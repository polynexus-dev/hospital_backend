"""
Support services the NABH HIS/EMR standard touches and MedNet sells as
separate modules: ambulance (COP.4.c — including en-route vitals visible
to the ED before arrival), CSSD sterilisation traceability, housekeeping
task management, and biomedical equipment / asset maintenance.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel

USER = settings.AUTH_USER_MODEL


# --- Ambulance -------------------------------------------------------------


class Ambulance(TenantScopedModel):
    class Kind(models.TextChoices):
        BLS = "bls", "Basic Life Support"
        ALS = "als", "Advanced Life Support"
        PATIENT_TRANSPORT = "transport", "Patient transport"
        NEONATAL = "neonatal", "Neonatal"

    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        ON_TRIP = "on_trip", "On trip"
        MAINTENANCE = "maintenance", "Under maintenance"

    vehicle_number = models.CharField(max_length=20)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.BLS)
    driver_name = models.CharField(max_length=100, blank=True)
    driver_phone = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.AVAILABLE)
    fitness_valid_until = models.DateField(null=True, blank=True)
    equipment_checklist = models.JSONField(default=dict, blank=True)
    device_token = models.CharField(max_length=64, blank=True, help_text="Token the in-vehicle monitor/app uses to post vitals & location.")

    class Meta:
        ordering = ["vehicle_number"]

    def __str__(self):
        return self.vehicle_number


class AmbulanceTrip(TenantScopedModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        DISPATCHED = "dispatched", "Dispatched"
        AT_SCENE = "at_scene", "At scene"
        EN_ROUTE = "en_route", "En route to hospital"
        ARRIVED = "arrived", "Arrived at ED"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    ambulance = models.ForeignKey(Ambulance, on_delete=models.PROTECT, related_name="trips")
    patient = models.ForeignKey("patients.Patient", on_delete=models.SET_NULL, null=True, blank=True, related_name="ambulance_trips")
    caller_name = models.CharField(max_length=150, blank=True)
    caller_phone = models.CharField(max_length=20, blank=True)
    pickup_address = models.TextField()
    destination = models.CharField(max_length=200, default="Emergency Department")
    trip_type = models.CharField(max_length=12, default="emergency", help_text="emergency | inter_facility | discharge | planned")
    chief_complaint = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REQUESTED)
    requested_at = models.DateTimeField(default=timezone.now)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    at_scene_at = models.DateTimeField(null=True, blank=True)
    departed_scene_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)
    paramedic = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    pre_hospital_notes = models.TextField(blank=True)
    eta_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    last_latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    last_longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    distance_km = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    charges = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    class Meta:
        ordering = ["-requested_at"]


class AmbulanceVitals(TenantScopedModel):
    """COP.4.c — transmitted from the ambulance to the ED in real time."""

    trip = models.ForeignKey(AmbulanceTrip, on_delete=models.CASCADE, related_name="vitals")
    recorded_at = models.DateTimeField(default=timezone.now)
    heart_rate = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_systolic = models.PositiveSmallIntegerField(null=True, blank=True)
    bp_diastolic = models.PositiveSmallIntegerField(null=True, blank=True)
    spo2 = models.PositiveSmallIntegerField(null=True, blank=True)
    respiratory_rate = models.PositiveSmallIntegerField(null=True, blank=True)
    temperature_c = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    gcs = models.PositiveSmallIntegerField(null=True, blank=True)
    blood_glucose = models.PositiveSmallIntegerField(null=True, blank=True)
    ecg_rhythm = models.CharField(max_length=60, blank=True)
    interventions = models.TextField(blank=True)
    source = models.CharField(max_length=10, default="manual", help_text="manual | device")

    class Meta:
        ordering = ["-recorded_at"]


# --- CSSD ------------------------------------------------------------------


class InstrumentSet(TenantScopedModel):
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=40)
    department = models.CharField(max_length=80, blank=True)
    items = models.JSONField(default=list, help_text="[{name, count}] — used for count verification on return.")
    sterilisation_method = models.CharField(max_length=20, default="steam", help_text="steam | eto | plasma | dry_heat")
    shelf_life_days = models.PositiveSmallIntegerField(default=30)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["hospital", "code"], name="unique_instrument_set_code")]

    def __str__(self):
        return f"{self.code} — {self.name}"


class SterilizationCycle(TenantScopedModel):
    class Result(models.TextChoices):
        PENDING = "pending", "Pending indicators"
        PASSED = "passed", "Passed"
        FAILED = "failed", "Failed — recall load"

    cycle_number = models.CharField(max_length=40)
    sterilizer = models.CharField(max_length=80)
    method = models.CharField(max_length=20, default="steam")
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    temperature_c = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    pressure_kpa = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    exposure_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    bowie_dick_passed = models.BooleanField(null=True)
    chemical_indicator_passed = models.BooleanField(null=True)
    biological_indicator_passed = models.BooleanField(null=True)
    result = models.CharField(max_length=8, choices=Result.choices, default=Result.PENDING)
    operator = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-started_at"]

    def save(self, *args, **kwargs):
        checks = [self.chemical_indicator_passed, self.biological_indicator_passed]
        if any(c is False for c in checks) or self.bowie_dick_passed is False:
            self.result = self.Result.FAILED
        elif all(c is True for c in checks):
            self.result = self.Result.PASSED
        super().save(*args, **kwargs)


class SterileBatch(TenantScopedModel):
    """One sterilised pack — traceable from cycle to the patient it was used on."""

    class Status(models.TextChoices):
        STERILE = "sterile", "Sterile / in store"
        ISSUED = "issued", "Issued"
        USED = "used", "Used"
        RETURNED = "returned", "Returned for reprocessing"
        EXPIRED = "expired", "Expired"
        RECALLED = "recalled", "Recalled"

    instrument_set = models.ForeignKey(InstrumentSet, on_delete=models.PROTECT, related_name="batches")
    cycle = models.ForeignKey(SterilizationCycle, on_delete=models.PROTECT, related_name="batches")
    batch_label = models.CharField(max_length=60, editable=False)
    sterilised_on = models.DateField(default=timezone.localdate)
    expires_on = models.DateField(null=True, blank=True, help_text="Defaults to sterilised_on + the set's shelf life.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.STERILE)
    issued_to = models.CharField(max_length=100, blank=True, help_text="OT / ward / department")
    issued_at = models.DateTimeField(null=True, blank=True)
    used_for_patient = models.ForeignKey("patients.Patient", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    used_in_surgery = models.ForeignKey("ot.OTSchedule", on_delete=models.SET_NULL, null=True, blank=True, related_name="sterile_batches")
    returned_at = models.DateTimeField(null=True, blank=True)
    return_count_ok = models.BooleanField(null=True)
    return_remarks = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-sterilised_on"]

    def save(self, *args, **kwargs):
        if not self.batch_label:
            self.batch_label = f"{self.instrument_set.code}-{self.cycle.cycle_number}-{timezone.now():%H%M%S}"
        if not self.expires_on:
            from datetime import timedelta

            self.expires_on = self.sterilised_on + timedelta(days=self.instrument_set.shelf_life_days)
        super().save(*args, **kwargs)


# --- Housekeeping ----------------------------------------------------------


class HousekeepingTask(TenantScopedModel):
    class TaskType(models.TextChoices):
        ROUTINE = "routine", "Routine cleaning"
        TERMINAL = "terminal", "Terminal cleaning (post-discharge)"
        SPILL = "spill", "Blood / body-fluid spill"
        ISOLATION = "isolation", "Isolation room cleaning"
        LINEN = "linen", "Linen change"
        WASTE = "waste", "Biomedical waste collection"
        PEST = "pest", "Pest control"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In progress"
        DONE = "done", "Done"
        VERIFIED = "verified", "Verified by supervisor"

    task_type = models.CharField(max_length=10, choices=TaskType.choices)
    location = models.CharField(max_length=150)
    bed = models.ForeignKey("facilities.Bed", on_delete=models.SET_NULL, null=True, blank=True, related_name="housekeeping_tasks")
    priority = models.CharField(max_length=8, default="normal", help_text="low | normal | high | stat")
    assigned_to = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    requested_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    due_by = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)
    checklist = models.JSONField(default=dict, blank=True)
    waste_bags = models.JSONField(default=dict, blank=True, help_text='BMW by colour: {"yellow": 2, "red": 1, "white": 0, "blue": 1}')
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["status", "-created_at"]


# --- Equipment / biomedical asset maintenance -------------------------------


class EquipmentAsset(TenantScopedModel):
    class Status(models.TextChoices):
        IN_USE = "in_use", "In use"
        UNDER_MAINTENANCE = "maintenance", "Under maintenance"
        BREAKDOWN = "breakdown", "Breakdown"
        CONDEMNED = "condemned", "Condemned"

    asset_tag = models.CharField(max_length=40)
    name = models.CharField(max_length=150)
    category = models.CharField(max_length=60, blank=True, help_text="monitor, ventilator, defibrillator, infusion pump, X-ray ...")
    make = models.CharField(max_length=100, blank=True)
    model_number = models.CharField(max_length=100, blank=True)
    serial_number = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=150, blank=True)
    department = models.CharField(max_length=80, blank=True)
    purchase_date = models.DateField(null=True, blank=True)
    purchase_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    vendor = models.CharField(max_length=150, blank=True)
    warranty_until = models.DateField(null=True, blank=True)
    amc_vendor = models.CharField(max_length=150, blank=True)
    amc_type = models.CharField(max_length=10, blank=True, help_text="AMC | CMC")
    amc_until = models.DateField(null=True, blank=True)
    pm_frequency_days = models.PositiveSmallIntegerField(default=180, help_text="Preventive maintenance interval.")
    calibration_frequency_days = models.PositiveSmallIntegerField(null=True, blank=True)
    last_pm_on = models.DateField(null=True, blank=True)
    last_calibrated_on = models.DateField(null=True, blank=True)
    is_critical = models.BooleanField(default=False, help_text="Life-support / critical-care equipment.")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.IN_USE)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["hospital", "asset_tag"], name="unique_asset_tag_per_hospital")]

    def __str__(self):
        return f"{self.asset_tag} {self.name}"

    @property
    def next_pm_due(self):
        from datetime import timedelta

        base = self.last_pm_on or self.purchase_date
        return base + timedelta(days=self.pm_frequency_days) if base else None

    @property
    def next_calibration_due(self):
        from datetime import timedelta

        if not self.calibration_frequency_days:
            return None
        base = self.last_calibrated_on or self.purchase_date
        return base + timedelta(days=self.calibration_frequency_days) if base else None


class MaintenanceRecord(TenantScopedModel):
    class Kind(models.TextChoices):
        PREVENTIVE = "preventive", "Preventive maintenance"
        BREAKDOWN = "breakdown", "Breakdown / corrective"
        CALIBRATION = "calibration", "Calibration"
        INSTALLATION = "installation", "Installation / commissioning"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        CLOSED = "closed", "Closed"

    asset = models.ForeignKey(EquipmentAsset, on_delete=models.CASCADE, related_name="maintenance_records")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    reported_at = models.DateTimeField(default=timezone.now)
    reported_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    problem = models.TextField(blank=True)
    work_done = models.TextField(blank=True)
    engineer = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    closed_at = models.DateTimeField(null=True, blank=True)
    downtime_hours = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, editable=False)
    cost = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    certificate = models.FileField(upload_to="maintenance/", blank=True)

    class Meta:
        ordering = ["-reported_at"]
