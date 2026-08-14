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

    class Meta:
        ordering = ["-transfused_at"]

    def __str__(self):
        return f"Transfusion: Unit {self.blood_unit_id} to {self.patient}"
