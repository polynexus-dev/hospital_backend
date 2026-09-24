from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.core.models import TenantScopedModel
from apps.patients.models import Prescription

# Generic FK to whatever's under nursing care — apps.ipd.Admission today,
# emergency.EDVisit / icu.ICUAdmission once Phase 6 ships. One model
# covers all three because a nursing note's shape is identical regardless
# of ward/ICU/ED — see docs/erp/02-domain-model.md's `nursing` section for
# why this is a deliberate generic relation, not three near-duplicate
# per-encounter-type models.


class NursingNote(TenantScopedModel):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField(max_length=64)
    content_object = GenericForeignKey("content_type", "object_id")

    nurse = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    note = models.TextField()

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"Nursing note on {self.content_type.model}:{self.object_id}"


class MedicationAdministration(TenantScopedModel):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField(max_length=64)
    content_object = GenericForeignKey("content_type", "object_id")

    # Optional traceability back to the e-Rx this dose came from —
    # Prescription.medications is a JSONField list (see apps.patients),
    # not individually-addressable rows, so this points at the whole
    # prescription rather than a specific line item within it.
    prescription = models.ForeignKey(Prescription, on_delete=models.SET_NULL, null=True, blank=True, related_name="administrations")
    medication_name = models.CharField(max_length=255)
    dose = models.CharField(max_length=100, blank=True)
    nurse = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    administered_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    # eMAR (NABH MOM.3.a/b) — who the dose was actually given to, and how
    # that was confirmed at the bedside. `patient` is denormalised from the
    # generic content_object so identity checks and CDSS "what is this
    # patient currently on" don't have to resolve every generic relation.
    class Status(models.TextChoices):
        GIVEN = "given", "Given"
        HELD = "held", "Held"
        REFUSED = "refused", "Refused by patient"
        MISSED = "missed", "Missed"

    class IdentityMethod(models.TextChoices):
        WRISTBAND_SCAN = "wristband_scan", "Wristband barcode scan"
        TWO_IDENTIFIERS = "two_identifiers", "Two identifiers verbally confirmed"

    patient = models.ForeignKey("patients.Patient", on_delete=models.CASCADE, null=True, blank=True, related_name="medication_administrations")
    route = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.GIVEN)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    identity_method = models.CharField(max_length=20, choices=IdentityMethod.choices, blank=True)
    scanned_identifier = models.CharField(max_length=64, blank=True, help_text="UHID read off the wristband barcode.")
    identity_verified = models.BooleanField(default=False, editable=False)
    witnessed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+", help_text="Second nurse for high-risk drugs.")

    class Meta:
        ordering = ["-administered_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.medication_name} for {self.content_type.model}:{self.object_id}"


class IntakeOutput(TenantScopedModel):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.CharField(max_length=64)
    content_object = GenericForeignKey("content_type", "object_id")

    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    intake_ml = models.PositiveIntegerField(null=True, blank=True)
    output_ml = models.PositiveIntegerField(null=True, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-recorded_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"I/O for {self.content_type.model}:{self.object_id}"
