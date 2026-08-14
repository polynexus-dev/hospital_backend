from django.conf import settings
from django.db import models

from apps.core.models import FinalizableModel, TenantScopedModel
from apps.opd.models import InvestigationOrder
from apps.patients.models import Patient


class LabTest(TenantScopedModel):
    class Department(models.TextChoices):
        HEMATOLOGY = "hematology", "Hematology"
        BIOCHEMISTRY = "biochemistry", "Biochemistry"
        MICROBIOLOGY = "microbiology", "Microbiology"
        SEROLOGY = "serology", "Serology"
        PATHOLOGY = "pathology", "Pathology"
        OTHER = "other", "Other"

    name = models.CharField(max_length=200)
    code = models.CharField(max_length=32, blank=True)
    department = models.CharField(max_length=16, choices=Department.choices, default=Department.OTHER)
    reference_range = models.CharField(max_length=255, blank=True, help_text="e.g. '70-100 mg/dL' — free text, not parsed.")
    unit = models.CharField(max_length=32, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class LabTestPackage(TenantScopedModel):
    name = models.CharField(max_length=200)
    tests = models.ManyToManyField(LabTest, related_name="packages", blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class LabOrder(TenantScopedModel):
    class Status(models.TextChoices):
        ORDERED = "ordered", "Ordered"
        SAMPLE_COLLECTED = "sample_collected", "Sample Collected"
        PROCESSING = "processing", "Processing"
        RESULTED = "resulted", "Resulted"
        VERIFIED = "verified", "Verified"

    # Nullable — orders can also originate from ipd/emergency once those
    # apps issue their own investigation requests, not only opd.
    investigation_order = models.ForeignKey(InvestigationOrder, on_delete=models.SET_NULL, null=True, blank=True, related_name="lab_orders")
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="lab_orders")
    ordered_tests = models.ManyToManyField(LabTest, related_name="orders", blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ORDERED)
    ordered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    ordered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-ordered_at"]

    def __str__(self):
        return f"Lab order for {self.patient} ({self.get_status_display()})"


class SampleCollection(TenantScopedModel):
    lab_order = models.ForeignKey(LabOrder, on_delete=models.CASCADE, related_name="sample_collections")
    sample_type = models.CharField(max_length=100, help_text="e.g. Blood, Urine, Swab")
    barcode = models.CharField(max_length=64, unique=True)
    collected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    collected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-collected_at"]

    def __str__(self):
        return f"{self.sample_type} ({self.barcode})"


class LabResult(TenantScopedModel, FinalizableModel):
    """"Verify" (lab industry term for pathologist sign-off/release) and
    "finalize" (this codebase's generic lock-against-edits term, see
    docs/erp/07-audit-and-security.md §2b) are the same transition here —
    collapsed into one FinalizableModel.finalize() call rather than a
    two-step gate, exposed via a `verify` action/permission at the API
    layer so the terminology matches how lab staff actually talk about it.
    See docs/erp/02-domain-model.md for why this simplifies the original
    "verify_labresult + finalize_labresult" two-permission sketch."""

    FINALIZED_LOCKED_FIELDS = ("value", "flag")

    class Flag(models.TextChoices):
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        LOW = "low", "Low"
        CRITICAL = "critical", "Critical"

    lab_order = models.ForeignKey(LabOrder, on_delete=models.CASCADE, related_name="results")
    lab_test = models.ForeignKey(LabTest, on_delete=models.CASCADE, related_name="results")
    value = models.CharField(max_length=100, blank=True)
    unit = models.CharField(max_length=32, blank=True)
    reference_range = models.CharField(max_length=255, blank=True)
    flag = models.CharField(max_length=16, choices=Flag.choices, default=Flag.NORMAL)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("verify_labresult", "Can verify (finalize) a lab result"),
        ]

    def __str__(self):
        return f"{self.lab_test}: {self.value} ({self.get_flag_display()})"
