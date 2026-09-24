"""Laundry & linen management (NABH HIC — linen handling; FMS support services).

Soiled and infected linen are collected from a ward in batches, washed
(in-house or by a vendor) and returned clean. Infected linen must get a
validated thermal wash (≥ 71 °C for ≥ 3 min) or a recorded chemical
disinfection. Returned counts go back into the ward's clean stock; anything
not returned is a loss, anything rejected (torn, stained) is condemned.
"""
from django.conf import settings
from django.db import models

from apps.core.models import TenantScopedModel

THERMAL_MIN_TEMP_C = 71
THERMAL_MIN_MINUTES = 3


class LinenType(TenantScopedModel):
    class Category(models.TextChoices):
        PATIENT = "patient", "Patient linen"
        OT = "ot", "OT linen / drapes"
        STAFF = "staff", "Staff uniforms"
        OTHER = "other", "Other"

    name = models.CharField(max_length=100)
    category = models.CharField(max_length=10, choices=Category.choices, default=Category.PATIENT)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2, default=0, help_text="Replacement cost per piece")
    expected_washes = models.PositiveSmallIntegerField(default=150, help_text="Typical life in wash cycles")
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "support_services"
        ordering = ["category", "name"]
        constraints = [models.UniqueConstraint(fields=["hospital", "name"], name="unique_linen_type_name")]

    def __str__(self):
        return self.name


class LinenStock(TenantScopedModel):
    """Clean linen held at a ward (or the central linen store when ward is empty)."""

    ward = models.ForeignKey("facilities.Ward", on_delete=models.CASCADE, null=True, blank=True, related_name="linen_stock")
    linen_type = models.ForeignKey(LinenType, on_delete=models.CASCADE, related_name="stock")
    par_level = models.PositiveIntegerField(default=0, help_text="Clean pieces the location should hold")
    clean_qty = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = "support_services"
        ordering = ["ward__name", "linen_type__name"]
        constraints = [
            models.UniqueConstraint(fields=["hospital", "ward", "linen_type"], name="unique_linen_stock_location"),
            # NULL wards are distinct to the database, so the central store needs its own rule.
            models.UniqueConstraint(fields=["hospital", "linen_type"], condition=models.Q(ward__isnull=True), name="unique_central_linen_stock"),
        ]

    @property
    def shortfall(self):
        return max(self.par_level - self.clean_qty, 0)


class LaundryBatch(TenantScopedModel):
    class Kind(models.TextChoices):
        SOILED = "soiled", "Soiled"
        INFECTED = "infected", "Infected / isolation (red bag)"

    class Status(models.TextChoices):
        COLLECTED = "collected", "Collected"
        PROCESSED = "processed", "Washed"
        RETURNED = "returned", "Returned"

    batch_number = models.CharField(max_length=30, blank=True)
    ward = models.ForeignKey("facilities.Ward", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.SOILED)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.COLLECTED)
    vendor = models.CharField(max_length=150, blank=True, help_text="Outsourced laundry; blank = in-house")
    rate_per_kg = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    weight_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    collected_at = models.DateTimeField(auto_now_add=True)
    collected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    wash_temp_c = models.PositiveSmallIntegerField(null=True, blank=True)
    wash_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    disinfectant = models.CharField(max_length=100, blank=True, help_text="e.g. 1% sodium hypochlorite, 30 min")
    processed_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    returned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    notes = models.TextField(blank=True)

    class Meta:
        app_label = "support_services"
        ordering = ["-collected_at"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.batch_number:
            self.batch_number = f"LB{self.collected_at:%y%m%d}-{self.pk}"
            super().save(update_fields=["batch_number"])

    @property
    def cost(self):
        return (self.weight_kg or 0) * self.rate_per_kg


class LaundryBatchLine(models.Model):
    batch = models.ForeignKey(LaundryBatch, on_delete=models.CASCADE, related_name="lines")
    linen_type = models.ForeignKey(LinenType, on_delete=models.PROTECT, related_name="+")
    sent_qty = models.PositiveIntegerField()
    returned_qty = models.PositiveIntegerField(default=0)
    rejected_qty = models.PositiveIntegerField(default=0, help_text="Condemned — torn, stained beyond use")

    class Meta:
        app_label = "support_services"
        constraints = [models.UniqueConstraint(fields=["batch", "linen_type"], name="unique_linen_type_per_batch")]

    @property
    def lost_qty(self):
        return max(self.sent_qty - self.returned_qty - self.rejected_qty, 0)
