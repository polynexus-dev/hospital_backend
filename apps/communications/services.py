import logging
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.utils import timezone

from apps.patients.models import Patient, record_timeline_event

from .adapters import SendRefused, get_provider_for_channel
from .models import Channel, ConsentOptOut, ContactSuppression, Message, Template, Thread

logger = logging.getLogger("communications.services")

INBOUND_SLA_HOURS = 2
UNSUBSCRIBE_SALT = "unsubscribe"


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


# --- Marketing opt-outs that aren't tied to a patient record --------------


def contact_hash(channel: str, address: str) -> str:
    from apps.core.encryption import blind_index, email_index, normalize_phone

    if not address:
        return ""
    return email_index(address) if channel == Channel.EMAIL else blind_index(normalize_phone(address))


def is_suppressed(hospital_id, channel: str, address: str) -> bool:
    digest = contact_hash(channel, address)
    return bool(digest) and ContactSuppression.objects.filter(hospital_id=hospital_id, channel=channel, contact_hash=digest).exists()


def unsubscribe_url(hospital_id, channel: str, address: str) -> str:
    token = signing.dumps({"h": str(hospital_id), "c": channel, "a": contact_hash(channel, address)}, salt=UNSUBSCRIBE_SALT, compress=True)
    return f"{settings.PUBLIC_APP_URL.rstrip('/')}/api/v1/unsubscribe/{token}/"


def suppress(hospital_id, channel: str, digest: str, *, source: str):
    """Stops marketing to this number / address on this channel: for leads
    (ContactSuppression) and for any patient with it (ConsentOptOut)."""
    if not digest:
        return
    ContactSuppression.objects.get_or_create(hospital_id=hospital_id, channel=channel, contact_hash=digest, defaults={"source": source})
    lookup = {"email_hash": digest} if channel == Channel.EMAIL else {"mobile_hash": digest}
    for patient in Patient.objects.filter(hospital_id=hospital_id, **lookup):
        ConsentOptOut.objects.update_or_create(
            hospital_id=hospital_id, patient=patient, channel=channel, purpose=ConsentOptOut.Purpose.MARKETING, defaults={"is_opted_out": True},
        )


# --- Sending ---------------------------------------------------------------


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


def render(template, context):
    """(body, subject, error): error names a placeholder the template uses
    but nobody filled, so the message is logged as failed, not sent."""
    try:
        return template.render(context), template.render_subject(context), ""
    except (KeyError, IndexError, ValueError) as exc:
        return template.body, template.subject, f"Template '{template.name}' uses {exc} which isn't filled in."[:255]


def deliver(*, hospital, channel, to, body, subject="", template=None, context=None, patient=None, enquiry=None,
            sent_by=None, campaign=None, marketing=False, purpose="", error="") -> Message:
    """Sends one message through the channel's provider and logs it (inbox,
    thread, patient timeline). Never raises: a refusal or provider error is
    stored on the Message (status=failed, error) for staff to see. A
    message passed in with `error` already set is logged without sending."""
    if error:
        return _log(hospital, channel, body, template, patient, enquiry, sent_by, campaign, Message.Status.FAILED, "", error, purpose)
    headers = {}
    if marketing and channel == Channel.EMAIL:
        url = unsubscribe_url(hospital.pk, channel, to)
        body = f"{body}\n\n--\nDon't want these emails? Unsubscribe: {url}"
        headers = {"List-Unsubscribe": f"<{url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
    error = ""
    try:
        provider_message_id = get_provider_for_channel(channel).send(to=to, body=body, subject=subject, template=template,
                                                                     context=context, headers=headers)
        send_status = Message.Status.SENT
    except SendRefused as exc:
        provider_message_id, send_status, error = "", Message.Status.FAILED, str(exc)[:255]
    except Exception as exc:
        logger.exception("Failed to send %s message (patient %s, enquiry %s)", channel, getattr(patient, "pk", None), getattr(enquiry, "pk", None))
        provider_message_id, send_status, error = "", Message.Status.FAILED, f"Provider error: {exc}"[:255]
    return _log(hospital, channel, body, template, patient, enquiry, sent_by, campaign, send_status, provider_message_id, error, purpose)


def _log(hospital, channel, body, template, patient, enquiry, sent_by, campaign, send_status, provider_message_id, error, purpose):
    message = Message.objects.create(
        hospital=hospital, patient=patient, enquiry=None if patient else enquiry, channel=channel, direction=Message.Direction.OUTBOUND,
        template=template, body=body, status=send_status, sent_by=sent_by, provider_message_id=provider_message_id or "",
        campaign=campaign, error=error, sent_at=timezone.now() if send_status == Message.Status.SENT else None,
    )
    if patient is not None:
        touch_thread(patient, channel, inbound=False)
        record_timeline_event(patient=patient, event_type="message", summary=f"{channel} sent: {purpose or (campaign and campaign.title) or 'message'}",
                              occurred_at=message.created_at, source=message, created_by=sent_by)
    else:
        thread, _ = Thread.objects.get_or_create(hospital=hospital, enquiry=enquiry, channel=channel)
        thread.last_message_at = timezone.now()
        thread.save(update_fields=["last_message_at"])
    return message


def send_message(*, patient, channel: str, purpose: str, context: dict, sent_by=None, fallback_channel: str | None = None,
                 marketing: bool = False) -> Message | None:
    """Renders the purpose+channel+language template, sends it through the
    configured provider, and logs it to the unified inbox + patient
    timeline (§5). Falls back to `fallback_channel` (e.g. SMS after
    WhatsApp) when opted out, no template exists, or the address is
    missing — matching the "DLT-registered SMS fallback" requirement."""
    to = _recipient_address(patient, channel)
    opted_out = is_opted_out(patient, channel, purpose) or (
        marketing and (is_opted_out(patient, channel, ConsentOptOut.Purpose.MARKETING) or is_suppressed(patient.hospital_id, channel, to)))
    if opted_out:
        return _fallback_or_none(patient, channel, purpose, context, sent_by, fallback_channel, marketing)

    template = get_active_template(patient.hospital, purpose=purpose, channel=channel, language=patient.preferred_language)
    if template is None or not to:
        return _fallback_or_none(patient, channel, purpose, context, sent_by, fallback_channel, marketing)

    context = {"hospital_name": patient.hospital.name, **context}
    body, subject, error = render(template, context)
    message = deliver(hospital=patient.hospital, channel=channel, to=to, body=body, subject=subject, template=template, context=context,
                      patient=patient, sent_by=sent_by, marketing=marketing, purpose=purpose, error=error)
    if message.status == Message.Status.FAILED:
        return _fallback_or_none(patient, channel, purpose, context, sent_by, fallback_channel, marketing) or message
    return message


def _fallback_or_none(patient, failed_channel, purpose, context, sent_by, fallback_channel, marketing=False):
    if fallback_channel and fallback_channel != failed_channel:
        return send_message(patient=patient, channel=fallback_channel, purpose=purpose, context=context, sent_by=sent_by, marketing=marketing)
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


def send_to_enquiry(*, enquiry, channel: str, purpose: str, context: dict | None = None, sent_by=None, fallback_channel: str | None = None,
                    marketing: bool = True) -> Message | None:
    """send_message for a lead who isn't a registered patient yet (workflow
    follow-ups). Uses the same templates; skips lost enquiries and numbers /
    addresses that have opted out of marketing on that channel."""
    from apps.clinical.notify import ensure_default_templates
    from apps.enquiries.models import Enquiry

    if enquiry.patient_id:
        return send_message(patient=enquiry.patient, channel=channel, purpose=purpose, context={"patient_name": enquiry.patient.full_name, **(context or {})},
                            sent_by=sent_by, fallback_channel=fallback_channel, marketing=marketing)

    def fallback():
        if fallback_channel and fallback_channel != channel:
            return send_to_enquiry(enquiry=enquiry, channel=fallback_channel, purpose=purpose, context=context, sent_by=sent_by, marketing=marketing)
        return None

    to = enquiry.email if channel == Channel.EMAIL else enquiry.mobile
    if enquiry.stage == Enquiry.Stage.LOST or not to or channel not in (Channel.WHATSAPP, Channel.SMS, Channel.EMAIL):
        return fallback()
    if channel == Channel.EMAIL:
        patients = Patient.objects.filter(hospital_id=enquiry.hospital_id).by_email(to)
    else:
        patients = Patient.objects.filter(hospital_id=enquiry.hospital_id).by_mobile(to)
    opted_out = ConsentOptOut.objects.filter(
        patient__in=patients, channel=channel, is_opted_out=True, purpose__in=[ConsentOptOut.Purpose.MARKETING, ConsentOptOut.Purpose.ALL],
    ).exists() or is_suppressed(enquiry.hospital_id, channel, to)
    if opted_out:
        return fallback()
    ensure_default_templates(enquiry.hospital)
    template = get_active_template(enquiry.hospital, purpose=purpose, channel=channel, language=Template.Language.ENGLISH)
    if template is None:
        return fallback()
    ctx = {"patient_name": enquiry.name, "hospital_name": enquiry.hospital.name, **(context or {})}
    body, subject, error = render(template, ctx)
    message = deliver(hospital=enquiry.hospital, channel=channel, to=to, body=body, subject=subject, template=template, context=ctx,
                      enquiry=enquiry, sent_by=sent_by, marketing=marketing, purpose=purpose, error=error)
    if message.status == Message.Status.FAILED:
        return fallback() or message
    return message
