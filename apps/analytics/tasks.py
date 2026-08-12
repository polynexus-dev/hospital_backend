from celery import shared_task
from django.utils import timezone

from apps.communications.adapters import get_whatsapp_provider
from apps.core.models import Hospital

from .models import DailyMISLog
from .services import daily_mis_summary, render_daily_mis_text


@shared_task
def send_daily_mis_to_owners():
    """Runs once a day on Celery beat — the owner's daily WhatsApp MIS
    (§1/§12), the single most-cited exit criterion in the Phase 1 scope."""
    sent = 0
    for hospital in Hospital.objects.filter(is_active=True):
        summary = daily_mis_summary(hospital)
        log = DailyMISLog.objects.filter(hospital=hospital, report_date=summary["date"]).first()
        if log is None:
            log = DailyMISLog(hospital=hospital, report_date=summary["date"])
        log.summary = summary

        if not hospital.owner_mis_whatsapp_number:
            log.send_error = "No owner_mis_whatsapp_number configured for this hospital."
            log.save()
            continue

        try:
            get_whatsapp_provider().send(to=hospital.owner_mis_whatsapp_number, body=render_daily_mis_text(hospital, summary))
            log.sent_at = timezone.now()
            log.send_error = ""
            sent += 1
        except Exception as exc:  # noqa: BLE001 — this is a best-effort notification job
            log.send_error = str(exc)
        log.save()
    return sent
