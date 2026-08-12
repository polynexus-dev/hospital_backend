from django.db import models

from apps.core.models import TenantScopedModel
from apps.patients.models import Patient


class HISVisit(TenantScopedModel):
    """Read-only cache of visit/admission/diagnostic history pulled from the
    hospital's HIS (§3, §15). Populated by apps.integrations.connectors —
    the concrete HL7/FHIR/DB-view/SFTP adapter is hospital-specific and not
    selected yet, so this ships empty until one is wired up."""

    class VisitType(models.TextChoices):
        OPD = "opd", "OPD"
        IPD = "ipd", "IPD"
        DIAGNOSTIC = "diagnostic", "Diagnostic"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="his_visits")
    external_visit_id = models.CharField(max_length=128)
    visit_type = models.CharField(max_length=16, choices=VisitType.choices)
    visit_date = models.DateTimeField()
    department_name = models.CharField(max_length=150, blank=True)
    doctor_name = models.CharField(max_length=150, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["hospital", "external_visit_id"], name="unique_his_visit_per_hospital"),
        ]
        ordering = ["-visit_date"]

    def __str__(self):
        return f"{self.patient} visit {self.external_visit_id}"


class HISBillingRecord(TenantScopedModel):
    """Outstanding dues / payment status sourced from HIS billing (§3)."""

    class Status(models.TextChoices):
        PAID = "paid", "Paid"
        PARTIAL = "partial", "Partially paid"
        OUTSTANDING = "outstanding", "Outstanding"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="his_billing_records")
    external_bill_id = models.CharField(max_length=128)
    bill_date = models.DateField()
    total_amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=Status.choices)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["hospital", "external_bill_id"], name="unique_his_bill_per_hospital"),
        ]
        ordering = ["-bill_date"]

    @property
    def outstanding_amount(self):
        return self.total_amount - self.paid_amount

    def __str__(self):
        return f"{self.patient} bill {self.external_bill_id} ({self.status})"
