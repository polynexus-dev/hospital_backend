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
    "payment_link": "Dear {patient_name}, please pay Rs. {amount} to {hospital_name} securely by UPI, card or netbanking: {link}",
    "enquiry_follow_up": "Hello {patient_name}, thank you for contacting {hospital_name}. We'd like to help with your enquiry: reply here or call us to book a convenient time.",
    "no_show_recall": "Dear {patient_name}, we missed you at your appointment at {hospital_name}. Reply here or call us to book a new time that suits you.",
    "homecare_booking": "Dear {patient_name}, your {service} home visit is booked for {time}.",
    "booking_link": "Dear {patient_name}, thank you for calling {hospital_name}. Book your appointment here: {link}",
    "enquiry_follow_up_email": "Dear {patient_name},\n\nThank you for getting in touch with {hospital_name}. We haven't heard back from you and "
                               "wanted to check whether we can still help.\n\nReply to this email or call us, and we'll find a time that suits you."
                               "\n\nWarm regards,\n{hospital_name}",
}

# Email subjects; purposes without one get "Message from {hospital_name}".
EMAIL_SUBJECTS = {
    "enquiry_follow_up": "Your enquiry with {hospital_name}",
    "enquiry_follow_up_email": "Can we still help? - {hospital_name}",
    "no_show_recall": "We missed you at {hospital_name}",
    "report_ready": "Your report is ready - {hospital_name}",
    "booking_link": "Book your appointment - {hospital_name}",
    "payment_link": "Payment link - {hospital_name}",
}
# OTPs only ever go by SMS / WhatsApp.
NO_EMAIL = {"portal_otp", "registration_otp"}


def ensure_default_templates(hospital):
    from apps.communications.models import Template

    expected = (len(DEFAULT_TEMPLATES) * 2 + len(set(DEFAULT_TEMPLATES) - NO_EMAIL)) * 3
    if Template.objects.filter(hospital=hospital, purpose__in=list(DEFAULT_TEMPLATES)).count() >= expected:
        return
    # English text is seeded for every language as a working fallback;
    # hospitals replace the hi/mr bodies with their own translations.
    for purpose, body in DEFAULT_TEMPLATES.items():
        for channel in ("whatsapp", "sms", "email"):
            if channel == "email" and purpose in NO_EMAIL:
                continue
            for language in ("en", "hi", "mr"):
                Template.objects.get_or_create(
                    hospital=hospital, purpose=purpose, channel=channel, language=language,
                    defaults={"name": f"{purpose.replace('_', ' ').title()} ({language})", "body": body,
                              "subject": EMAIL_SUBJECTS.get(purpose, "Message from {hospital_name}") if channel == "email" else ""},
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
