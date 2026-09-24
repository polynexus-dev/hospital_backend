from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel
from apps.ipd.models import Admission
from apps.patients.models import Patient


class Donor(TenantScopedModel):
    name = models.CharField(max_length=255)
    blood_group = models.CharField(max_length=8)
    phone = models.CharField(max_length=20, blank=True)
    last_donation_date = models.DateField(null=True, blank=True)
    # COP.3.a prospective donor record.
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=10, blank=True)
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    haemoglobin = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    is_eligible = models.BooleanField(default=True)
    deferral_reason = models.CharField(max_length=255, blank=True)
    deferred_until = models.DateField(null=True, blank=True)
    is_voluntary = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"Donor: {self.name} ({self.blood_group})"


class BloodUnit(TenantScopedModel):
    class Component(models.TextChoices):
        WHOLE_BLOOD = "whole_blood", "Whole Blood"
        PRBC = "prbc", "Packed Red Blood Cells (PRBC)"
        FFP = "ffp", "Fresh Frozen Plasma (FFP)"
        PLATELETS = "platelets", "Platelets"

    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        RESERVED = "reserved", "Reserved"
        ISSUED = "issued", "Issued"
        DISCARDED = "discarded", "Discarded"

    donor = models.ForeignKey(Donor, on_delete=models.SET_NULL, null=True, blank=True, related_name="units")
    blood_group = models.CharField(max_length=8)
    component = models.CharField(max_length=32, choices=Component.choices, default=Component.WHOLE_BLOOD)
    collection_date = models.DateField()
    expiry_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.AVAILABLE)
    unit_number = models.CharField(max_length=40, blank=True, help_text="Bag / segment number.")
    volume_ml = models.PositiveIntegerField(null=True, blank=True)
    tti_screened = models.BooleanField(default=False, help_text="HIV, HBV, HCV, syphilis, malaria screening done and non-reactive.")
    storage_location = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ["expiry_date"]

    def __str__(self):
        return f"Blood Unit: {self.blood_group} ({self.get_component_display()}) - {self.get_status_display()}"


class CrossMatchRequest(TenantScopedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        MATCHED = "matched", "Matched"
        FAILED = "failed", "Failed"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="cross_match_requests")
    blood_group_required = models.CharField(max_length=8)
    component = models.CharField(max_length=32, choices=BloodUnit.Component.choices, default=BloodUnit.Component.WHOLE_BLOOD)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    # COP.3.b turnaround time — request (created_at) → each sub-activity → issue.
    units_requested = models.PositiveSmallIntegerField(default=1)
    urgency = models.CharField(max_length=10, default="routine", help_text="routine | urgent | emergency")
    sample_received_at = models.DateTimeField(null=True, blank=True)
    grouping_done_at = models.DateTimeField(null=True, blank=True)
    crossmatched_at = models.DateTimeField(null=True, blank=True)
    reserved_unit = models.ForeignKey("BloodUnit", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    issued_at = models.DateTimeField(null=True, blank=True)
    delay_reason = models.CharField(max_length=255, blank=True)

    TAT_TARGET_MINUTES = {"routine": 120, "urgent": 60, "emergency": 30}

    @property
    def turnaround_minutes(self):
        if not self.issued_at:
            return None
        return round((self.issued_at - self.created_at).total_seconds() / 60, 1)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"CrossMatch for {self.patient} ({self.blood_group_required})"


class Transfusion(TenantScopedModel):
    blood_unit = models.ForeignKey(BloodUnit, on_delete=models.CASCADE, related_name="transfusions")
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="transfusions")
    admission = models.ForeignKey(Admission, on_delete=models.SET_NULL, null=True, blank=True, related_name="transfusions")
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    transfused_at = models.DateTimeField(default=timezone.now)
    reaction_notes = models.TextField(blank=True)
    # COP.3.d safe transfusion + haemovigilance.
    bedside_verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", help_text="Second person for the two-person bedside check.")
    pre_vitals = models.JSONField(default=dict, blank=True)
    post_vitals = models.JSONField(default=dict, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    had_reaction = models.BooleanField(default=False)
    reaction_type = models.CharField(max_length=40, blank=True, help_text="febrile | allergic | haemolytic | TRALI | TACO | anaphylaxis | other")
    reaction_severity = models.CharField(max_length=10, blank=True)

    class Meta:
        ordering = ["-transfused_at"]

    def __str__(self):
        return f"Transfusion: Unit {self.blood_unit_id} to {self.patient}"
