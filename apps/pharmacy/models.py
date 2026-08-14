from django.conf import settings
from django.db import models

from apps.core.models import TenantScopedModel
from apps.patients.models import Prescription


class Supplier(TenantScopedModel):
    """Minimal — deliberately not the real supplier/procurement model.
    Phase 7's `inventory` app owns that; this exists only so
    MedicineBatch has somewhere to point until then, per
    docs/erp/02-domain-model.md's pharmacy section. Expect this to be
    migrated onto `inventory.Supplier` (and this model retired) when
    Phase 7 ships, not extended in place."""

    name = models.CharField(max_length=200)
    contact_person = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Medicine(TenantScopedModel):
    class Form(models.TextChoices):
        TABLET = "tablet", "Tablet"
        CAPSULE = "capsule", "Capsule"
        SYRUP = "syrup", "Syrup"
        INJECTION = "injection", "Injection"
        OINTMENT = "ointment", "Ointment"
        OTHER = "other", "Other"

    name = models.CharField(max_length=200)
    generic_name = models.CharField(max_length=200, blank=True)
    form = models.CharField(max_length=16, choices=Form.choices, default=Form.TABLET)
    unit = models.CharField(max_length=32, blank=True, help_text="e.g. strip of 10, bottle of 100ml")
    reorder_level = models.PositiveIntegerField(default=0, help_text="Total quantity_available across batches at/below this triggers a low-stock flag.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class MedicineBatch(TenantScopedModel):
    medicine = models.ForeignKey(Medicine, on_delete=models.CASCADE, related_name="batches")
    batch_number = models.CharField(max_length=100)
    expiry_date = models.DateField()
    quantity_available = models.PositiveIntegerField(default=0)
    mrp = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    purchase_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name="batches")

    class Meta:
        ordering = ["expiry_date"]
        constraints = [
            models.UniqueConstraint(fields=["medicine", "batch_number"], name="unique_batch_number_per_medicine"),
        ]

    def __str__(self):
        return f"{self.medicine} — batch {self.batch_number}"


class DispenseRecord(TenantScopedModel):
    # Nullable — OTC/walk-in dispensing has no prescription behind it.
    prescription = models.ForeignKey(Prescription, on_delete=models.SET_NULL, null=True, blank=True, related_name="dispense_records")
    batch = models.ForeignKey(MedicineBatch, on_delete=models.PROTECT, related_name="dispense_records")
    quantity = models.PositiveIntegerField()
    dispensed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    dispensed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-dispensed_at"]

    def __str__(self):
        return f"{self.quantity} x {self.batch.medicine} dispensed"


class StockAdjustment(TenantScopedModel):
    class AdjustmentType(models.TextChoices):
        DAMAGE = "damage", "Damage"
        EXPIRY = "expiry", "Expiry"
        CORRECTION = "correction", "Correction"

    batch = models.ForeignKey(MedicineBatch, on_delete=models.CASCADE, related_name="adjustments")
    adjustment_type = models.CharField(max_length=16, choices=AdjustmentType.choices)
    # Signed — a correction can go either direction; damage/expiry are
    # conventionally negative but not enforced as such, since a hospital
    # correcting a previous over-deduction needs a positive delta too.
    quantity_delta = models.IntegerField()
    reason = models.TextField(blank=True)
    adjusted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    adjusted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-adjusted_at"]

    def __str__(self):
        return f"{self.get_adjustment_type_display()} {self.quantity_delta:+d} on {self.batch}"
