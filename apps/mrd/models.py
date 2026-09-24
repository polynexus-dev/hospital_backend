"""
Medical Records Department (MRD): ICD-10 master & clinical coding (NABH
IMS.1.e), physical file tracking with issue/return, record completeness
audit, and retention / archival / destruction scheduling.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel


class ICD10Code(models.Model):
    """Platform-wide terminology (not tenant data). Seeded with common
    Indian hospital diagnoses; the full WHO/CBHI list is loaded with
    `manage.py import_icd10 <csv>`."""

    code = models.CharField(max_length=10, unique=True)
    title = models.CharField(max_length=300)
    chapter = models.CharField(max_length=120, blank=True)
    is_billable = models.BooleanField(default=True)
    snomed_code = models.CharField(max_length=20, blank=True, help_text="Optional SNOMED CT concept mapping.")

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} {self.title}"


class MedicalRecordFile(TenantScopedModel):
    """One physical/electronic case file — typically per IPD admission."""

    class Status(models.TextChoices):
        PENDING = "pending", "Awaiting receipt from ward"
        IN_MRD = "in_mrd", "In MRD"
        ISSUED = "issued", "Issued"
        ARCHIVED = "archived", "Archived (off-site)"
        DESTROYED = "destroyed", "Destroyed"
        MISSING = "missing", "Missing"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, related_name="record_files")
    admission = models.OneToOneField("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="record_file")
    file_number = models.CharField(max_length=40, editable=False)
    is_mlc = models.BooleanField(default=False)
    rack_location = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    received_at = models.DateTimeField(null=True, blank=True)
    retention_until = models.DateField(null=True, blank=True)
    destroyed_at = models.DateTimeField(null=True, blank=True)
    destruction_approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["hospital", "file_number"], name="unique_mrd_file_number")]

    RETENTION_YEARS = {"ipd": 3, "mlc": 10, "minor": 21}

    def save(self, *args, **kwargs):
        if not self.file_number:
            year = timezone.localdate().year
            n = MedicalRecordFile.objects.filter(hospital_id=self.hospital_id, file_number__startswith=f"MRD{year}").count() + 1
            self.file_number = f"MRD{year}{n:06d}"
        if not self.retention_until:
            years = self.RETENTION_YEARS["mlc"] if self.is_mlc else self.RETENTION_YEARS["ipd"]
            self.retention_until = timezone.localdate().replace(year=timezone.localdate().year + years)
        super().save(*args, **kwargs)


class FileMovement(TenantScopedModel):
    record_file = models.ForeignKey(MedicalRecordFile, on_delete=models.CASCADE, related_name="movements")
    issued_to = models.CharField(max_length=150)
    department = models.CharField(max_length=80, blank=True)
    purpose = models.CharField(max_length=150, help_text="Follow-up, audit, court, insurance, research ...")
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    issued_at = models.DateTimeField(default=timezone.now)
    due_back = models.DateField()
    returned_at = models.DateTimeField(null=True, blank=True)
    received_back_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-issued_at"]


class CodingRecord(TenantScopedModel):
    """IMS.1.e — ICD-10 coding of an episode for morbidity/mortality stats."""

    admission = models.OneToOneField("ipd.Admission", on_delete=models.CASCADE, related_name="coding")
    principal_diagnosis = models.ForeignKey(ICD10Code, on_delete=models.PROTECT, related_name="+")
    secondary_diagnoses = models.ManyToManyField(ICD10Code, blank=True, related_name="+")
    procedures = models.JSONField(default=list, blank=True, help_text="[{code, description}] — ICD-10-PCS / CPT")
    cause_of_death = models.ForeignKey(ICD10Code, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    coded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class RecordAudit(TenantScopedModel):
    """Completeness audit of a case file (checklist scored automatically
    from what exists in the system + auditor's manual items)."""

    record_file = models.ForeignKey(MedicalRecordFile, on_delete=models.CASCADE, related_name="audits")
    checklist = models.JSONField(default=dict)
    score_pct = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    deficiencies = models.TextField(blank=True)
    auditor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
