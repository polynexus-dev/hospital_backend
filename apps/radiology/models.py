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
    code = models.CharField(max_length=40, blank=True, help_text="LOINC / SNOMED procedure code.")
    uses_contrast = models.BooleanField(default=False)
    preparation_instructions = models.TextField(blank=True)
    duration_minutes = models.PositiveSmallIntegerField(default=15)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.get_modality_display()})"


class RadiologyOrder(TenantScopedModel):
    class Status(models.TextChoices):
        ORDERED = "ordered", "Ordered"
        SCHEDULED = "scheduled", "Scheduled"
        ARRIVED = "arrived", "Patient arrived"
        IN_PROGRESS = "in_progress", "Scan in progress"
        COMPLETED = "completed", "Completed"
        REPORTED = "reported", "Reported"
        CANCELLED = "cancelled", "Cancelled"

    investigation_order = models.ForeignKey(InvestigationOrder, on_delete=models.SET_NULL, null=True, blank=True, related_name="radiology_orders")
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="radiology_orders")
    procedure = models.ForeignKey(RadiologyProcedure, on_delete=models.CASCADE, related_name="orders")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ORDERED)
    ordered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    ordered_at = models.DateTimeField(auto_now_add=True)

    # NABH AAC.4.b unique request ID, AAC.4.e status tracking, AAC.4.l
    # contraindication override, AAC.4.n outsourcing, IMS.1.g DICOM.
    accession_number = models.CharField(max_length=40, blank=True, editable=False, db_index=True)
    priority = models.CharField(max_length=8, default="routine", help_text="routine | urgent | stat")
    clinical_history = models.TextField(blank=True)
    status_history = models.JSONField(default=list, blank=True, editable=False)
    contraindication_override_reason = models.CharField(max_length=255, blank=True)
    is_outsourced = models.BooleanField(default=False)
    outsourced_center = models.CharField(max_length=150, blank=True)
    outsourced_report_file = models.FileField(upload_to="radiology_outsourced/%Y/%m/", blank=True)
    study_instance_uid = models.CharField(max_length=128, blank=True)
    patient_notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-ordered_at"]

    def __str__(self):
        return f"{self.procedure} for {self.patient} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        from django.utils import timezone

        if not self.accession_number and self.hospital_id:
            day = timezone.localdate()
            n = RadiologyOrder.objects.filter(hospital_id=self.hospital_id, ordered_at__date=day).count() + 1
            self.accession_number = f"RAD{day:%y%m%d}{n:04d}"
        if not self.status_history or self.status_history[-1].get("status") != self.status:
            self.status_history = (self.status_history or []) + [{"status": self.status, "at": timezone.now().isoformat()}]
        super().save(*args, **kwargs)


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
    template = models.ForeignKey("RadiologyTemplate", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    is_amended = models.BooleanField(default=False, editable=False)
    addendum = models.TextField(blank=True)
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



class RadiologyTemplate(TenantScopedModel):
    """AAC.4.d — normal/structured report templates per modality."""

    name = models.CharField(max_length=150)
    modality = models.CharField(max_length=16, choices=RadiologyProcedure.Modality.choices)
    procedure = models.ForeignKey(RadiologyProcedure, on_delete=models.SET_NULL, null=True, blank=True, related_name="templates")
    findings = models.TextField()
    impression = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["modality", "name"]


class RadiologyEquipment(TenantScopedModel):
    """AAC.4.g — slots are generated from each machine's working hours."""

    name = models.CharField(max_length=120)
    modality = models.CharField(max_length=16, choices=RadiologyProcedure.Modality.choices)
    room = models.CharField(max_length=60, blank=True)
    start_time = models.TimeField(default="08:00")
    end_time = models.TimeField(default="20:00")
    slot_minutes = models.PositiveSmallIntegerField(default=15)
    ae_title = models.CharField(max_length=16, blank=True, help_text="DICOM AE title for modality worklist.")
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.get_modality_display()})"


class RadiologyAppointment(TenantScopedModel):
    order = models.OneToOneField(RadiologyOrder, on_delete=models.CASCADE, related_name="appointment")
    equipment = models.ForeignKey(RadiologyEquipment, on_delete=models.PROTECT, related_name="appointments")
    start = models.DateTimeField()
    end = models.DateTimeField()
    technician = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["start"]


class RadiologyImage(TenantScopedModel):
    """IMS.1.g — DICOM (or key JPEG) images attached to a study."""

    order = models.ForeignKey(RadiologyOrder, on_delete=models.CASCADE, related_name="images")
    file = models.FileField(upload_to="dicom/%Y/%m/")
    is_dicom = models.BooleanField(default=False)
    study_instance_uid = models.CharField(max_length=128, blank=True)
    series_instance_uid = models.CharField(max_length=128, blank=True)
    sop_instance_uid = models.CharField(max_length=128, blank=True)
    modality = models.CharField(max_length=16, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    # Viewer attributes (rows, frames, window, spacing, series, instance no.) read once at upload.
    meta = models.JSONField(default=dict, blank=True)
