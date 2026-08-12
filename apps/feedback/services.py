from django.conf import settings
from django.utils import timezone

from apps.communications.models import Channel
from apps.communications.services import send_message

from .models import FeedbackRequest


def build_response_url(feedback_request: FeedbackRequest) -> str:
    base = getattr(settings, "PUBLIC_APP_URL", "").rstrip("/")
    return f"{base}/feedback/{feedback_request.token}/"


def send_feedback_request(feedback_request: FeedbackRequest):
    message = send_message(
        patient=feedback_request.patient,
        channel=Channel.WHATSAPP,
        purpose="feedback_request",
        context={
            "patient_name": feedback_request.patient.full_name,
            "doctor_name": str(feedback_request.doctor) if feedback_request.doctor else "",
            "response_url": build_response_url(feedback_request),
        },
        fallback_channel=Channel.SMS,
    )
    if message is not None:
        feedback_request.sent_at = timezone.now()
        feedback_request.save(update_fields=["sent_at"])
    return message
