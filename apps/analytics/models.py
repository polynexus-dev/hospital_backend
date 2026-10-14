from apps.core.models import TenantScopedModel
from django.db import models


class DailyMISLog(TenantScopedModel):
    """One row per day per hospital — what the owner's WhatsApp MIS (§12)
    said and when it went out. Kept so the numbers are reviewable in the
    admin/API, not just fired-and-forgotten over WhatsApp."""

    report_date = models.DateField()
    summary = models.JSONField(default=dict)
    sent_at = models.DateTimeField(null=True, blank=True)
    send_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-report_date"]
        constraints = [
            models.UniqueConstraint(fields=["hospital", "report_date"], name="unique_mis_log_per_hospital_per_day"),
        ]

    def __str__(self):
        return f"MIS {self.hospital} {self.report_date}"


class MISSchedule(TenantScopedModel):
    """Weekly / monthly MIS reports sent automatically (Reports > Scheduled
    MIS). Email gets the PDF; WhatsApp gets the headline numbers."""

    weekly_enabled = models.BooleanField(default=False)
    weekly_day = models.PositiveSmallIntegerField(default=0, help_text="0 = Monday ... 6 = Sunday; covers the previous Monday-Sunday.")
    monthly_enabled = models.BooleanField(default=False)
    email_recipients = models.JSONField(default=list, blank=True)
    whatsapp_recipients = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["hospital"], name="one_mis_schedule_per_hospital")]


class MISReportDelivery(TenantScopedModel):
    """One scheduled report: which period, who it went to, and any error."""

    class Period(models.TextChoices):
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"

    period = models.CharField(max_length=8, choices=Period.choices)
    period_start = models.DateField()
    period_end = models.DateField()
    sent_at = models.DateTimeField(null=True, blank=True)
    recipients = models.JSONField(default=list, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-period_start"]
        constraints = [models.UniqueConstraint(fields=["hospital", "period", "period_start"], name="one_mis_delivery_per_period")]
