from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import Department, TenantScopedModel
from apps.patients.models import Patient


class Doctor(TenantScopedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="doctor_profile")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="doctors")
    name = models.CharField(max_length=255)
    speciality = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    default_consultation_minutes = models.PositiveSmallIntegerField(default=15)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"Dr. {self.name}"


class SlotTemplate(TenantScopedModel):
    """A recurring weekly availability rule used to generate concrete Slot
    rows (§4 doctor-wise / department-wise slot definitions)."""

    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="slot_templates")
    weekday = models.IntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    slot_duration_minutes = models.PositiveSmallIntegerField(default=15)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["weekday", "start_time"]

    def __str__(self):
        return f"{self.doctor} - {self.get_weekday_display()} {self.start_time}-{self.end_time}"


class Slot(TenantScopedModel):
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="slots")
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_blocked = models.BooleanField(default=False, help_text="Manually blocked (leave, procedure, etc.), not bookable.")
    blocked_reason = models.CharField(max_length=255, blank=True, help_text="e.g. doctor on leave, OT block.")

    class Meta:
        ordering = ["date", "start_time"]
        constraints = [
            models.UniqueConstraint(fields=["doctor", "date", "start_time"], name="unique_slot_per_doctor_datetime"),
        ]
        indexes = [
            models.Index(fields=["hospital", "doctor", "date"]),
        ]

    def __str__(self):
        return f"{self.doctor} {self.date} {self.start_time}"

    @property
    def is_booked(self):
        return hasattr(self, "appointment") and self.appointment.status not in (
            Appointment.Status.CANCELLED,
            Appointment.Status.RESCHEDULED,
        )


class Appointment(TenantScopedModel):
    class Status(models.TextChoices):
        BOOKED = "booked", "Booked"
        CONFIRMED = "confirmed", "Confirmed"
        CHECKED_IN = "checked_in", "Checked in"
        IN_CONSULT = "in_consult", "In consult"
        DIAGNOSTICS = "diagnostics", "Diagnostics"
        COMPLETED = "completed", "Completed"
        NO_SHOW = "no_show", "No-show"
        CANCELLED = "cancelled", "Cancelled"
        RESCHEDULED = "rescheduled", "Rescheduled"

    class Source(models.TextChoices):
        CRM = "crm", "CRM / front desk"
        WHATSAPP = "whatsapp", "WhatsApp"
        WEBSITE = "website", "Website"
        PATIENT_PORTAL = "portal", "Patient portal / app"
        EXTERNAL = "external", "External system / aggregator"
        TELEPHONE = "telephone", "Telephone / IVR"
        WALK_IN = "walk_in", "Walk-in"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="appointments")
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="appointments")
    slot = models.OneToOneField(Slot, on_delete=models.PROTECT, related_name="appointment")

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.BOOKED)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.CRM)

    reason = models.CharField(max_length=255, blank=True)
    consent_captured = models.BooleanField(default=False)
    consent_captured_at = models.DateTimeField(null=True, blank=True)

    registration_token = models.CharField(max_length=128, unique=True, blank=True)

    # Walk-in / front-desk OPD queue display ("now serving #12") — assigned
    # sequentially per doctor per day at check-in, distinct from the
    # pre-booked slot time so same-day walk-ins queue naturally behind
    # earlier check-ins regardless of their booked slot time.
    queue_token = models.PositiveIntegerField(null=True, blank=True)

    reminder_24h_sent_at = models.DateTimeField(null=True, blank=True)
    reminder_2h_sent_at = models.DateTimeField(null=True, blank=True)

    checked_in_at = models.DateTimeField(null=True, blank=True)
    consult_started_at = models.DateTimeField(null=True, blank=True)  # NABH OPD waiting-time KPI (AAC.2.c)
    completed_at = models.DateTimeField(null=True, blank=True)
    external_reference = models.CharField(max_length=100, blank=True, help_text="Booking id in the external system (AAC.2.d: aggregator app, ABDM, partner portal).")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    no_show_at = models.DateTimeField(null=True, blank=True)

    rescheduled_from = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="rescheduled_to")

    booked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-slot__date", "-slot__start_time"]

    def __str__(self):
        return f"{self.patient} with {self.doctor} on {self.slot.date}"


class Waitlist(TenantScopedModel):
    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting"
        OFFERED = "offered", "Offered"
        BOOKED = "booked", "Booked"
        EXPIRED = "expired", "Expired"
        CANCELLED = "cancelled", "Cancelled"

    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name="waitlist_entries")
    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="waitlist_entries")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="waitlist_entries")
    preferred_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.WAITING)
    offered_slot = models.ForeignKey(Slot, on_delete=models.SET_NULL, null=True, blank=True, related_name="waitlist_offers")
    offered_at = models.DateTimeField(null=True, blank=True)
    resulting_appointment = models.ForeignKey(Appointment, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.patient} waiting for {self.doctor} ({self.get_status_display()})"


class OPDVisit(TenantScopedModel):
    """One OPD registration at the front desk: who, which doctor, new or
    follow-up, who pays and at what rates, who referred them, and the bill.
    The Appointment underneath carries the queue token and visit status."""

    class VisitType(models.TextChoices):
        NEW = "new", "New visit"
        FOLLOW_UP = "follow_up", "Follow-up (paid)"
        FREE_FOLLOW_UP = "free_follow_up", "Follow-up (free)"

    class CaseType(models.TextChoices):
        NEW_COMPLAINT = "new_complaint", "New complaint"
        OLD_COMPLAINT = "old_complaint", "Same complaint as before"

    class PayerType(models.TextChoices):
        SELF = "self", "Self-pay"
        CORPORATE = "corporate", "Corporate"
        TPA = "tpa", "TPA"
        INSURANCE = "insurance", "Insurance"
        SCHEME = "scheme", "Government scheme"
        HEALTH_CAMP = "health_camp", "Health camp"

    visit_number = models.CharField(max_length=24, blank=True, editable=False, db_index=True)
    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="opd_visits")
    doctor = models.ForeignKey(Doctor, on_delete=models.PROTECT, related_name="opd_visits")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    appointment = models.OneToOneField(Appointment, on_delete=models.PROTECT, related_name="opd_visit")
    visit_type = models.CharField(max_length=16, choices=VisitType.choices)
    case_type = models.CharField(max_length=16, choices=CaseType.choices, default=CaseType.NEW_COMPLAINT)
    is_new_patient = models.BooleanField(default=False)

    payer_type = models.CharField(max_length=12, choices=PayerType.choices, default=PayerType.SELF)
    rate_category = models.CharField(max_length=20, default="general", help_text="Tariff rate column, e.g. general, private, insurance, corporate.")
    corporate_client = models.ForeignKey("packages.CorporateClient", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    tpa = models.ForeignKey("tpa.TPACompany", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    insurance_policy = models.ForeignKey("finance.InsurancePolicy", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    scheme = models.ForeignKey("schemes.GovtScheme", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    campaign = models.ForeignKey("packages.Campaign", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")

    referred_by = models.ForeignKey("referrals.ReferringDoctor", on_delete=models.SET_NULL, null=True, blank=True, related_name="opd_visits")
    referral_note = models.CharField(max_length=255, blank=True)
    marketing_person = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
                                         help_text="For referral payouts only; gives no access to the patient's record.")

    bill = models.ForeignKey("billing.Bill", on_delete=models.SET_NULL, null=True, blank=True, related_name="opd_visits")
    registered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["hospital", "patient", "doctor"])]

    def save(self, *args, **kwargs):
        if not self.visit_number and self.hospital_id:
            day = timezone.localdate()
            n = OPDVisit.objects.filter(hospital_id=self.hospital_id, created_at__date=day).count() + 1
            self.visit_number = f"OP{day:%y%m%d}-{n:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.visit_number} {self.get_visit_type_display()}"
