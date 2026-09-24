from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TenantScopedModel
from apps.patients.models import Patient


class EDVisit(TenantScopedModel):
    class Status(models.TextChoices):
        TRIAGED = "triaged", "Triaged"
        IN_TREATMENT = "in_treatment", "In Treatment"
        ADMITTED = "admitted", "Admitted"
        DISCHARGED = "discharged", "Discharged"
        REFERRED_OUT = "referred_out", "Referred Out"
        DECEASED = "deceased", "Deceased"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="ed_visits")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.TRIAGED)
    chief_complaint = models.TextField(blank=True)
    arrived_at = models.DateTimeField(default=timezone.now)

    # COP.4.a — ED registration details (patients may arrive unidentified).
    mode_of_arrival = models.CharField(max_length=20, blank=True, help_text="walk_in | ambulance | referred | police")
    brought_by = models.CharField(max_length=150, blank=True)
    is_unidentified = models.BooleanField(default=False)
    ambulance_trip = models.ForeignKey("support_services.AmbulanceTrip", on_delete=models.SET_NULL, null=True, blank=True, related_name="ed_visits")
    disposition_at = models.DateTimeField(null=True, blank=True)

    # COP.4.b — medico-legal case.
    is_mlc = models.BooleanField(default=False)
    mlc_number = models.CharField(max_length=40, blank=True, editable=False)
    mlc_type = models.CharField(max_length=40, blank=True, help_text="RTA | assault | burns | poisoning | fall | hanging | sexual_assault | other")
    police_station = models.CharField(max_length=150, blank=True)
    police_intimated_at = models.DateTimeField(null=True, blank=True)
    police_officer_name = models.CharField(max_length=150, blank=True)
    injuries_description = models.TextField(blank=True)
    mlc_checklist = models.JSONField(default=dict, blank=True)
    mlc_marked_by = models.ForeignKey("accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    mlc_marked_at = models.DateTimeField(null=True, blank=True)

    MLC_CHECKLIST_ITEMS = [
        "police_intimation_sent", "injury_certificate_prepared", "clothing_and_samples_preserved",
        "identification_marks_recorded", "consent_for_examination", "photographs_taken_if_indicated", "dying_declaration_arranged_if_needed",
    ]

    class Meta:
        ordering = ["-arrived_at"]

    def __str__(self):
        return f"ED Visit: {self.patient} ({self.get_status_display()})"


class Triage(TenantScopedModel):
    class Category(models.TextChoices):
        RESUSCITATION = "1_resuscitation", "Category 1 - Resuscitation"
        EMERGENT = "2_emergent", "Category 2 - Emergent"
        URGENT = "3_urgent", "Category 3 - Urgent"
        LESS_URGENT = "4_less_urgent", "Category 4 - Less Urgent"
        NON_URGENT = "5_non_urgent", "Category 5 - Non Urgent"

    ed_visit = models.OneToOneField(EDVisit, on_delete=models.CASCADE, related_name="triage")
    triage_category = models.CharField(max_length=32, choices=Category.choices)
    vitals_summary = models.TextField(blank=True)
    triaged_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    triaged_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Triage: {self.ed_visit} - {self.get_triage_category_display()}"
