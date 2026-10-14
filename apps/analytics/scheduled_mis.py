"""Weekly and monthly MIS, sent automatically.

Every morning (CELERY_BEAT_SCHEDULE "send-scheduled-mis") each hospital with
a schedule is checked:
- weekly, on its chosen weekday: the previous Monday-Sunday;
- monthly, on the 1st: the previous calendar month.
Email recipients get the executive MIS PDF (the same one Reports > Export
builds) with the headline numbers in the body; WhatsApp recipients get the
headline numbers. Each period is sent once (MISReportDelivery); a failed
send is retried on the next morning's run.
"""
import logging
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.utils import timezone

from . import services
from .models import MISReportDelivery, MISSchedule

logger = logging.getLogger(__name__)


def previous_week(today: date):
    monday = today - timedelta(days=today.weekday() + 7)
    return monday, monday + timedelta(days=6)


def previous_month(today: date):
    last = today.replace(day=1) - timedelta(days=1)
    return last.replace(day=1), last


def _bounds(start: date, end: date):
    tz = timezone.get_current_timezone()
    return timezone.make_aware(datetime.combine(start, time.min), tz), timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)


def build_pdf(hospital, start: date, end: date, label: str) -> bytes:
    from .mis_pdf import render_mis_pdf

    s, e = _bounds(start, end)
    return render_mis_pdf(
        hospital=hospital, summary=services.daily_mis_summary(hospital, start=s, end=e),
        dept_doctor_rows=services.department_doctor_volume(hospital, s, e), revenue_rows=services.revenue_by_source(hospital, s, e).get("rows", []),
        start_date=start.isoformat(), end_date=end.isoformat(), period_label=label,
    )


def headline(hospital, start: date, end: date, label: str) -> str:
    s, e = _bounds(start, end)
    summary = services.daily_mis_summary(hospital, start=s, end=e)
    calls, funnel = summary["calls"], summary["enquiry_funnel"]
    return (
        f"{label} MIS: {hospital.name}, {start:%d %b} to {end:%d %b %Y}\n"
        f"Calls: {calls['received'] or 0} received, {calls['answered'] or 0} answered, {calls['missed'] or 0} missed\n"
        f"Enquiries: {sum(funnel.values())} new, {funnel.get('visited', 0)} visited, {funnel.get('completed', 0)} completed\n"
        f"No-shows: {summary['no_show']['no_shows']} ({summary['no_show']['recall_tasks_done']} recalled)\n"
        f"Pending callbacks: {summary['pending_callbacks']}"
    )


def send_report(schedule: MISSchedule, period: str, start: date, end: date) -> MISReportDelivery:
    from apps.communications.adapters import get_whatsapp_provider

    hospital = schedule.hospital
    label = "Weekly" if period == MISReportDelivery.Period.WEEKLY else "Monthly"
    delivery, _ = MISReportDelivery.objects.get_or_create(hospital=hospital, period=period, period_start=start, defaults={"period_end": end})
    delivery.sent_now = False
    if delivery.sent_at:
        return delivery
    text, errors, sent_to = headline(hospital, start, end, label), [], []
    if schedule.email_recipients:
        try:
            msg = EmailMessage(subject=f"{label} MIS: {hospital.name}, {start:%d %b} to {end:%d %b %Y}",
                               body=text + "\n\nThe full report is attached.", from_email=settings.DEFAULT_FROM_EMAIL, to=list(schedule.email_recipients))
            msg.attach(f"MIS_{label}_{start}_to_{end}.pdf", build_pdf(hospital, start, end, label), "application/pdf")
            msg.send()
            sent_to += list(schedule.email_recipients)
        except Exception as exc:  # noqa: BLE001 - best effort, recorded and retried
            logger.exception("%s MIS email failed for hospital %s", label, hospital.pk)
            errors.append(f"Email: {exc}")
    for number in schedule.whatsapp_recipients:
        try:
            get_whatsapp_provider().send(to=number, body=text)
            sent_to.append(number)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"WhatsApp {number}: {exc}")
    delivery.recipients, delivery.error, delivery.period_end = sent_to, "\n".join(errors), end
    if sent_to and not errors:
        delivery.sent_at = timezone.now()
    delivery.save()
    delivery.sent_now = delivery.sent_at is not None
    return delivery


def send_due(today: date | None = None) -> int:
    today = today or timezone.localdate()
    sent = 0
    for schedule in MISSchedule.objects.filter(hospital__is_active=True).select_related("hospital"):
        if not (schedule.email_recipients or schedule.whatsapp_recipients):
            continue
        if schedule.weekly_enabled and today.weekday() == schedule.weekly_day:
            sent += bool(send_report(schedule, MISReportDelivery.Period.WEEKLY, *previous_week(today)).sent_now)
        if schedule.monthly_enabled and today.day == 1:
            sent += bool(send_report(schedule, MISReportDelivery.Period.MONTHLY, *previous_month(today)).sent_now)
        # Retry anything that failed earlier.
        for failed in MISReportDelivery.objects.filter(hospital=schedule.hospital, sent_at__isnull=True, created_at__date__lt=today):
            if (failed.period == "weekly" and schedule.weekly_enabled) or (failed.period == "monthly" and schedule.monthly_enabled):
                sent += bool(send_report(schedule, failed.period, failed.period_start, failed.period_end).sent_now)
    return sent
