from django.conf import settings
from django.db import models

from apps.core.models import FinalizableModel, TenantScopedModel
from apps.opd.models import InvestigationOrder
from apps.patients.models import ALLOWED_DOCUMENT_EXTENSIONS, Patient, validate_document_size
from django.core.validators import FileExtensionValidator


class RadiologyProcedure(TenantScopedModel):
    class Modality(models.TextChoices):
        XRAY = "xray", "X-Ray"
        CT = "ct", "CT Scan"
        MRI = "mri", "MRI"
        USG = "usg", "Ultrasound"
        OTHER = "other", "Other"

    name = models.CharField(max_length=200)
    modality = models.CharField(max_length=16, choices=Modality.choices, default=Modality.XRAY)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.get_modality_display()})"


class RadiologyOrder(TenantScopedModel):
    class Status(models.TextChoices):
        ORDERED = "ordered", "Ordered"
        SCHEDULED = "scheduled", "Scheduled"
        COMPLETED = "completed", "Completed"
        REPORTED = "reported", "Reported"

    investigation_order = models.ForeignKey(InvestigationOrder, on_delete=models.SET_NULL, null=True, blank=True, related_name="radiology_orders")
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="radiology_orders")
    procedure = models.ForeignKey(RadiologyProcedure, on_delete=models.CASCADE, related_name="orders")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ORDERED)
    ordered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    ordered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-ordered_at"]

    def __str__(self):
        return f"{self.procedure} for {self.patient} ({self.get_status_display()})"


class RadiologyReport(TenantScopedModel, FinalizableModel):
    """Same "verify == finalize" collapse as apps.laboratory.LabResult —
    see that model's docstring. `verify_radiologyreport` is the exposed
    permission/action name; FinalizableModel's finalized_at/finalized_by
    back it."""

    FINALIZED_LOCKED_FIELDS = ("findings", "impression")

    radiology_order = models.OneToOneField(RadiologyOrder, on_delete=models.CASCADE, related_name="report")
    findings = models.TextField(blank=True)
    impression = models.TextField(blank=True)
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    # Singular, not the plural "image_files" originally sketched — a plain
    # FileField only ever holds one file; a full multi-image study would
    # need a child model (RadiologyImage), out of scope for what this
    # system needs beyond attaching the report's key image. Reuses
    # apps.patients' upload validators rather than redefining them.
    image_file = models.FileField(
        upload_to="radiology_reports/%Y/%m/", blank=True,
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_DOCUMENT_EXTENSIONS), validate_document_size],
    )

    class Meta:
        permissions = [
            ("verify_radiologyreport", "Can verify (finalize) a radiology report"),
        ]

    def __str__(self):
        return f"Radiology report for {self.radiology_order}"
