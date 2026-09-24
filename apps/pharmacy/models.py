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

    # NABH MOM.1.a / MOM.2.c/e/f safety & formulary flags, IMS.1.h coding.
    strength = models.CharField(max_length=60, blank=True)
    route = models.CharField(max_length=40, blank=True)
    category = models.CharField(max_length=80, blank=True, help_text="Therapeutic class, e.g. Antibiotic, Antihypertensive")
    drug_code = models.CharField(max_length=40, blank=True, help_text="SNOMED CT / NRCeS drug registry code.")
    is_formulary = models.BooleanField(default=True)
    is_high_risk = models.BooleanField(default=False, help_text="High-alert medication (insulin, heparin, KCl, opioids, chemo...).")
    is_lasa = models.BooleanField(default=False, help_text="Look-alike / sound-alike.")
    lasa_pair = models.CharField(max_length=200, blank=True)
    is_emergency = models.BooleanField(default=False)
    is_controlled = models.BooleanField(default=False, help_text="NDPS schedule — narcotic register.")
    is_restricted_antimicrobial = models.BooleanField(default=False)
    storage = models.CharField(max_length=60, blank=True, help_text="e.g. 2–8 °C")

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
    is_quarantined = models.BooleanField(default=False, help_text="Recalled / on hold — cannot be dispensed.")

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

    # MOM.2.b/c/f/g — who it went to, the second check for high-risk
    # drugs, allergy override, and the non-formulary flag.
    patient = models.ForeignKey("patients.Patient", on_delete=models.SET_NULL, null=True, blank=True, related_name="dispense_records")
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    is_non_formulary = models.BooleanField(default=False)
    override_reason = models.CharField(max_length=255, blank=True)

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



class MedicineReturn(TenantScopedModel):
    """MOM.2.j — returns from wards/patients back into stock (or to waste)."""

    batch = models.ForeignKey(MedicineBatch, on_delete=models.PROTECT, related_name="returns")
    patient = models.ForeignKey("patients.Patient", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    quantity = models.PositiveIntegerField()
    reason = models.CharField(max_length=255)
    restocked = models.BooleanField(default=True, help_text="False = destroyed / not fit for reuse.")
    returned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]


class MedicineRecall(TenantScopedModel):
    """MOM.2.j — manufacturer/regulator recall: quarantines batches and
    lists every patient they were dispensed to."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT, related_name="recalls")
    batches = models.ManyToManyField(MedicineBatch, related_name="recalls", blank=True)
    reason = models.TextField()
    reference = models.CharField(max_length=100, blank=True, help_text="CDSCO / manufacturer notice number.")
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)
    initiated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class MedicationReconciliation(TenantScopedModel):
    """MOM.2.h — at admission/transfer/discharge. items: [{name, dose,
    frequency, source: home|hospital, decision: continue|stop|modify|new, note}]."""

    class Stage(models.TextChoices):
        ADMISSION = "admission", "Admission"
        TRANSFER = "transfer", "Transfer"
        DISCHARGE = "discharge", "Discharge"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="med_reconciliations")
    admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="med_reconciliations")
    stage = models.CharField(max_length=10, choices=Stage.choices)
    items = models.JSONField(default=list)
    discrepancies_found = models.PositiveSmallIntegerField(default=0, editable=False)
    reconciled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        self.discrepancies_found = sum(1 for i in self.items if i.get("decision") in ("stop", "modify"))
        super().save(*args, **kwargs)


class PharmacyIndent(TenantScopedModel):
    """MOM.2.a — ward/department requisition to pharmacy. items:
    [{medicine: id, quantity, issued_quantity}]"""

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        PARTIAL = "partial", "Partially issued"
        ISSUED = "issued", "Issued"
        REJECTED = "rejected", "Rejected"

    indent_number = models.CharField(max_length=30, editable=False, blank=True)
    department = models.CharField(max_length=100)
    patient = models.ForeignKey("patients.Patient", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="pharmacy_indents")
    items = models.JSONField(default=list)
    priority = models.CharField(max_length=8, default="routine")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    issued_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.indent_number and self.hospital_id:
            n = PharmacyIndent.objects.filter(hospital_id=self.hospital_id).count() + 1
            self.indent_number = f"IND{n:06d}"
        super().save(*args, **kwargs)


class EmergencyMedicationStock(TenantScopedModel):
    """MOM.4.a — crash-cart / emergency drug stock per location."""

    location = models.CharField(max_length=100, help_text="e.g. ER crash cart 1, ICU, Ward 3")
    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT, related_name="emergency_stocks")
    par_level = models.PositiveIntegerField(default=1)
    current_quantity = models.PositiveIntegerField(default=0)
    expiry_date = models.DateField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["location", "medicine__name"]
        constraints = [models.UniqueConstraint(fields=["hospital", "location", "medicine"], name="unique_emergency_med_per_location")]

    def save(self, *args, **kwargs):
        was_stocked = True
        if self.pk:
            prev = EmergencyMedicationStock.objects.filter(pk=self.pk).values_list("current_quantity", flat=True).first()
            was_stocked = bool(prev)
        super().save(*args, **kwargs)
        if self.current_quantity == 0 and was_stocked:
            StockOutEvent.objects.create(hospital_id=self.hospital_id, medicine=self.medicine, location=self.location, is_emergency_medication=True)
        elif self.current_quantity > 0:
            from django.utils import timezone

            StockOutEvent.objects.filter(hospital_id=self.hospital_id, medicine=self.medicine, location=self.location, resolved_at__isnull=True).update(resolved_at=timezone.now())


class StockOutEvent(TenantScopedModel):
    """NABH KPI 'stock-outs of emergency medications'."""

    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT, related_name="stock_outs")
    location = models.CharField(max_length=100, default="Main pharmacy")
    is_emergency_medication = models.BooleanField(default=False)
    occurred_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
