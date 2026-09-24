"""Patient-facing notifications for clinical events (report ready,
teleconsult link, token called, claim status...). Goes through the
existing communications pipeline (template per purpose/channel/language,
WhatsApp with SMS fallback, opt-out respected, logged to the inbox and
patient timeline). Never raises — a failed notification must not fail
the clinical action that triggered it."""
import logging

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATES = {
    "report_ready": "Dear {patient_name}, your {report_type} report ({order_number}) is ready. View it on the patient portal or collect it from the hospital.",
    "teleconsult_link": "Dear {patient_name}, your video consultation with {doctor_name} is at {time}. Join: {link}",
    "token_called": "Dear {patient_name}, token {token} — please proceed to {counter}.",
    "claim_status": "Dear {patient_name}, your insurance claim {claim_number} status: {status}.",
    "discharge_ready": "Dear {patient_name}, your discharge summary is ready on the patient portal.",
    "portal_otp": "{otp} is your OTP to sign in to the patient portal. Valid for 10 minutes. Do not share it.",
    "registration_otp": "{otp} is your OTP to verify your mobile number with {hospital_name}.",
    "homecare_booking": "Dear {patient_name}, your {service} home visit is booked for {time}.",
}


def ensure_default_templates(hospital):
    from apps.communications.models import Template

    if Template.objects.filter(hospital=hospital, purpose__in=list(DEFAULT_TEMPLATES)).count() >= len(DEFAULT_TEMPLATES) * 6:
        return
    # English text is seeded for every language as a working fallback;
    # hospitals replace the hi/mr bodies with their own translations.
    for purpose, body in DEFAULT_TEMPLATES.items():
        for channel in ("whatsapp", "sms"):
            for language in ("en", "hi", "mr"):
                Template.objects.get_or_create(
                    hospital=hospital, purpose=purpose, channel=channel, language=language,
                    defaults={"name": f"{purpose.replace('_', ' ').title()} ({language})", "body": body},
                )


def notify_patient(patient, purpose, context, *, sent_by=None):
    from django.db import transaction

    try:
        from apps.communications.services import send_message

        with transaction.atomic():  # savepoint: a failure here can't poison the caller's transaction
            ensure_default_templates(patient.hospital)
            ctx = {"patient_name": patient.full_name, "hospital_name": patient.hospital.name, **context}
            return send_message(patient=patient, channel="whatsapp", purpose=purpose, context=ctx, sent_by=sent_by, fallback_channel="sms")
    except Exception:
        logger.exception("Patient notification %s failed for patient %s", purpose, getattr(patient, "pk", None))
        return None
