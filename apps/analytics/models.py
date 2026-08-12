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
