import logging
from datetime import timedelta

from django.utils import timezone

from apps.patients.models import record_timeline_event

from .adapters import get_provider_for_channel
from .models import Channel, ConsentOptOut, Message, Template, Thread

logger = logging.getLogger("communications.services")

INBOUND_SLA_HOURS = 2


def is_opted_out(patient, channel: str, purpose: str = ConsentOptOut.Purpose.TRANSACTIONAL) -> bool:
    return ConsentOptOut.objects.filter(
        patient=patient, channel=channel, is_opted_out=True, purpose__in=[purpose, ConsentOptOut.Purpose.ALL]
    ).exists()


def get_active_template(hospital, *, purpose: str, channel: str, language: str) -> Template | None:
    return (
        Template.objects.filter(hospital=hospital, purpose=purpose, channel=channel, language=language, is_active=True).first()
        or Template.objects.filter(hospital=hospital, purpose=purpose, channel=channel, language=Template.Language.ENGLISH, is_active=True).first()
    )


def _recipient_address(patient, channel: str) -> str:
    if channel == Channel.EMAIL:
        return patient.email
    return patient.mobile


def touch_thread(patient, channel: str, *, inbound: bool) -> Thread:
    """Keeps the per-patient-per-channel `Thread` (Inbox activity/SLA
    timestamps) in sync with the flat `Message` log. Inbound messages start
    (or restart) the SLA clock; a staff reply clears it."""
    thread, _ = Thread.objects.get_or_create(hospital=patient.hospital, patient=patient, channel=channel)
    now = timezone.now()
    thread.last_message_at = now
    if inbound:
        thread.last_inbound_at = now
        thread.sla_due_at = now + timedelta(hours=INBOUND_SLA_HOURS)
    else:
        thread.sla_due_at = None
    thread.save()
    return thread


def send_message(*, patient, channel: str, purpose: str, context: dict, sent_by=None, fallback_channel: str | None = None) -> Message | None:
    """Renders the purpose+channel+language template, sends it through the
    configured provider, and logs it to the unified inbox + patient
    timeline (§5). Falls back to `fallback_channel` (e.g. SMS after
    WhatsApp) when opted out, no template exists, or the address is
    missing — matching the "DLT-registered SMS fallback" requirement."""
    if is_opted_out(patient, channel, purpose):
        return _fallback_or_none(patient, channel, purpose, context, sent_by, fallback_channel)

    template = get_active_template(patient.hospital, purpose=purpose, channel=channel, language=patient.preferred_language)
    to = _recipient_address(patient, channel)
    if template is None or not to:
        return _fallback_or_none(patient, channel, purpose, context, sent_by, fallback_channel)

    body = template.render(context)
    provider = get_provider_for_channel(channel)
    try:
        provider_message_id = provider.send(to=to, body=body, subject=template.subject)
        send_status = Message.Status.SENT
    except Exception:
        logger.exception("Failed to send %s message to patient %s", channel, patient.pk)
        provider_message_id = ""
        send_status = Message.Status.FAILED

    message = Message.objects.create(
        hospital=patient.hospital,
        patient=patient,
        channel=channel,
        direction=Message.Direction.OUTBOUND,
        template=template,
        body=body,
        status=send_status,
        sent_by=sent_by,
        provider_message_id=provider_message_id,
        sent_at=timezone.now() if send_status == Message.Status.SENT else None,
    )
    touch_thread(patient, channel, inbound=False)
    record_timeline_event(
        patient=patient,
        event_type="message",
        summary=f"{channel} sent: {purpose}",
        occurred_at=message.created_at,
        source=message,
        created_by=sent_by,
    )
    if send_status == Message.Status.FAILED:
        return _fallback_or_none(patient, channel, purpose, context, sent_by, fallback_channel) or message
    return message


def _fallback_or_none(patient, failed_channel, purpose, context, sent_by, fallback_channel):
    if fallback_channel and fallback_channel != failed_channel:
        return send_message(patient=patient, channel=fallback_channel, purpose=purpose, context=context, sent_by=sent_by)
    return None


def send_appointment_reminder(appointment, *, offset_hours: int) -> Message | None:
    purpose = f"appointment_reminder_{offset_hours}h"
    context = {
        "patient_name": appointment.patient.full_name,
        "doctor_name": str(appointment.doctor),
        "date": appointment.slot.date.isoformat(),
        "time": appointment.slot.start_time.strftime("%H:%M"),
    }
    return send_message(
        patient=appointment.patient, channel=Channel.WHATSAPP, purpose=purpose, context=context, fallback_channel=Channel.SMS
    )
