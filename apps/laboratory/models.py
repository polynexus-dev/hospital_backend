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

    # NABH IMS.1.f LOINC coding; AAC.3.i numeric ranges drive auto-flagging
    # and COP.1.m critical-value alerts.
    loinc_code = models.CharField(max_length=20, blank=True)
    sample_type = models.CharField(max_length=60, blank=True, help_text="e.g. Serum, EDTA whole blood, Urine")
    container = models.CharField(max_length=60, blank=True, help_text="e.g. Red top, Lavender top")
    ref_low = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    ref_high = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    critical_low = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    critical_high = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    tat_hours = models.PositiveSmallIntegerField(null=True, blank=True)
    method = models.CharField(max_length=120, blank=True)
    report_template = models.ForeignKey("LabReportTemplate", on_delete=models.SET_NULL, null=True, blank=True, related_name="tests")
    is_outsourced = models.BooleanField(default=False, help_text="Routinely sent to an external lab.")

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

    # NABH AAC.3 / COP.1.f CPOE.
    order_number = models.CharField(max_length=40, blank=True, editable=False, db_index=True)
    priority = models.CharField(max_length=8, default="routine", help_text="routine | urgent | stat")
    clinical_notes = models.TextField(blank=True)
    admission = models.ForeignKey("ipd.Admission", on_delete=models.SET_NULL, null=True, blank=True, related_name="lab_orders")
    patient_notified_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.order_number and self.hospital_id:
            from django.utils import timezone

            day = timezone.localdate()
            n = LabOrder.objects.filter(hospital_id=self.hospital_id, ordered_at__date=day).count() + 1
            self.order_number = f"LAB{day:%y%m%d}{n:04d}"
        super().save(*args, **kwargs)

    class Meta:
        ordering = ["-ordered_at"]

    def __str__(self):
        return f"Lab order for {self.patient} ({self.get_status_display()})"


class SampleCollection(TenantScopedModel):
    lab_order = models.ForeignKey(LabOrder, on_delete=models.CASCADE, related_name="sample_collections")
    sample_type = models.CharField(max_length=100, help_text="e.g. Blood, Urine, Swab")

    # AAC.3.c/h specimen tracking & rejection.
    class Status(models.TextChoices):
        COLLECTED = "collected", "Collected"
        IN_TRANSIT = "in_transit", "In transit"
        RECEIVED = "received", "Received in lab"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.COLLECTED)
    received_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    rejection_reason = models.CharField(max_length=40, blank=True, help_text="haemolysed | clotted | insufficient | mislabelled | leaked | wrong_container | delayed | other")
    rejection_notes = models.CharField(max_length=255, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    # Not globally unique=True — barcode is free-text/user-supplied (no
    # per-hospital namespacing like Patient.uhid gets from the hospital
    # slug), so a global constraint would wrongly stop two different
    # hospitals both labelling a sample "BC-0001".
    barcode = models.CharField(max_length=64, blank=True, help_text="Specimen number — auto-assigned when left blank (AAC.3.b).")
    collected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    collected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-collected_at"]
        constraints = [
            models.UniqueConstraint(fields=["hospital", "barcode"], name="unique_samplecollection_barcode_per_hospital"),
        ]

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

    # AAC.3.j repeat flag; AAC.3.f/K02 amendments; device source.
    needs_repeat = models.BooleanField(default=False)
    repeat_reason = models.CharField(max_length=255, blank=True)
    repeated_from = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="repeats")
    is_amended = models.BooleanField(default=False, editable=False)
    source = models.CharField(max_length=10, default="manual", help_text="manual | analyzer")
    interpretation = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("verify_labresult", "Can verify (finalize) a lab result"),
        ]

    def __str__(self):
        return f"{self.lab_test}: {self.value} ({self.get_flag_display()})"



class LabReportTemplate(TenantScopedModel):
    """AAC.3.d — report layout per department/test group."""

    name = models.CharField(max_length=150)
    department = models.CharField(max_length=16, choices=LabTest.Department.choices, default=LabTest.Department.OTHER)
    header_text = models.TextField(blank=True)
    footer_text = models.TextField(blank=True, help_text="Method, disclaimers, interpretation notes printed below results.")
    signatory_designation = models.CharField(max_length=120, default="Consultant Pathologist")
    show_previous_result = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class LabReportAddendum(TenantScopedModel):
    """AAC.3.f — text appended to an already-released report."""

    lab_order = models.ForeignKey(LabOrder, on_delete=models.CASCADE, related_name="addenda")
    text = models.TextField()
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        ordering = ["created_at"]


class OutsourcedLabTest(TenantScopedModel):
    """AAC.3.m — tests referred to an external lab and their results."""

    class Status(models.TextChoices):
        PENDING_DISPATCH = "pending", "Pending dispatch"
        SENT = "sent", "Sent"
        RESULT_RECEIVED = "received", "Result received"
        CANCELLED = "cancelled", "Cancelled"

    lab_order = models.ForeignKey(LabOrder, on_delete=models.CASCADE, related_name="outsourced_tests")
    lab_test = models.ForeignKey(LabTest, on_delete=models.PROTECT, related_name="+")
    external_lab = models.CharField(max_length=150)
    external_reference = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING_DISPATCH)
    sent_at = models.DateTimeField(null=True, blank=True)
    expected_by = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    result_text = models.TextField(blank=True)
    result_file = models.FileField(upload_to="outsourced_reports/%Y/%m/", blank=True)

    class Meta:
        ordering = ["-created_at"]


class LabAnalyzer(TenantScopedModel):
    """AAC.1.l / device integration — an analyser that pushes results
    (HL7 v2 ORU^R01) into the LIS with its own token."""

    name = models.CharField(max_length=120)
    model_name = models.CharField(max_length=120, blank=True)
    protocol = models.CharField(max_length=10, default="hl7", help_text="hl7 | astm")
    api_token = models.CharField(max_length=64, blank=True)
    test_code_map = models.JSONField(default=dict, blank=True, help_text='Analyser test code -> LabTest.code, e.g. {"GLU": "FBS"}')
    is_active = models.BooleanField(default=True)
    last_message_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name
