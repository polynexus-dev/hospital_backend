"""AI analysis of recorded calls: transcript, summary, call reason, lead
class and booking intent.

Both models are self-hosted, so call audio and transcripts never leave the
hospital's own servers (DPDP Act 2023):
- speech-to-text: a Whisper server with the OpenAI-compatible
  /v1/audio/transcriptions endpoint (e.g. speaches / faster-whisper-server),
  at settings.WHISPER_BASE_URL;
- the summary and labels: the same Ollama server the 24x7 assistant uses
  (apps.communications.llm_router).

Output is for staff only. It never reaches the caller and never drives a
clinical decision; a wrong label costs a misfiled call, nothing more.
Unset WHISPER_BASE_URL turns the whole feature off.
"""
import json
import logging

import requests
from django.conf import settings
from django.utils import timezone

from .models import Call

logger = logging.getLogger(__name__)

MAX_RECORDING_BYTES = 50 * 1024 * 1024
MIN_DURATION_SECONDS = 10  # shorter answered calls have nothing to summarise

_SYSTEM_PROMPT = (
    "You read transcripts of phone calls to an Indian hospital's front desk. The caller may speak English, Hindi or "
    "Marathi. Reply with nothing but a JSON object with these keys:\n"
    "- summary: two or three sentences in English for the hospital's staff: who called, what they wanted, what was "
    "agreed and any follow-up promised. No medical advice, no diagnosis.\n"
    "- call_reason: one of opd, surgical, second_opinion, billing, report, complaint, other.\n"
    "- lead_class: hot (wants to book or be treated soon), warm (interested, undecided or comparing), cold (general "
    "information only), not_a_lead (existing patient admin, wrong number, vendor, spam).\n"
    "- booking_intent: true if the caller wanted an appointment, admission or procedure booked, otherwise false.\n"
    "Use only what the transcript says."
)

REASONS = {c.value for c in Call.CallReason}
LEAD_CLASSES = {c.value for c in Call.LeadClass}


class CallAIError(Exception):
    pass


def enabled():
    return bool(getattr(settings, "WHISPER_BASE_URL", ""))


def should_analyse(call):
    return (enabled() and not call.ai_status and call.recording_url and call.status == Call.Status.ANSWERED
            and call.duration_seconds >= MIN_DURATION_SECONDS)


def transcribe(recording_url):
    try:
        audio = requests.get(recording_url, timeout=60, stream=True, verify=settings.CALL_RECORDING_VERIFY_TLS)
        audio.raise_for_status()
        chunks, size = [], 0
        for chunk in audio.iter_content(64 * 1024):
            size += len(chunk)
            if size > MAX_RECORDING_BYTES:
                raise CallAIError("Recording is larger than 50 MB.")
            chunks.append(chunk)
    except requests.RequestException as exc:
        raise CallAIError(f"Couldn't download the recording: {type(exc).__name__}") from exc
    try:
        response = requests.post(
            f"{settings.WHISPER_BASE_URL.rstrip('/')}/v1/audio/transcriptions",
            files={"file": ("call.wav", b"".join(chunks))},
            data={"model": settings.WHISPER_MODEL, "response_format": "json"},
            timeout=settings.CALL_AI_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return (response.json().get("text") or "").strip()
    except (requests.RequestException, ValueError) as exc:
        raise CallAIError(f"Transcription server error: {type(exc).__name__}") from exc


def summarise(transcript):
    try:
        response = requests.post(
            f"{settings.OLLAMA_BASE_URL}/api/chat",
            json={
                "model": settings.OLLAMA_MODEL, "stream": False, "format": "json",
                "messages": [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": transcript[:12000]}],
            },
            timeout=settings.CALL_AI_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = json.loads(response.json()["message"]["content"])
    except (requests.RequestException, KeyError, ValueError) as exc:
        raise CallAIError(f"Summary model error: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise CallAIError("Summary model returned something other than an object.")
    booking = data.get("booking_intent")
    return {
        "summary": str(data.get("summary") or "").strip()[:2000],
        "call_reason": data.get("call_reason") if data.get("call_reason") in REASONS else "",
        "lead_class": data.get("lead_class") if data.get("lead_class") in LEAD_CLASSES else "",
        "booking_intent": booking if isinstance(booking, bool) else None,
    }


def analyse_call(call):
    """Fills the AI fields. Never raises: a failure is recorded on the call
    (ai_status=failed, ai_error) and can be retried from the call log."""
    if not call.recording_url:
        call.ai_status, call.ai_error = Call.AIStatus.SKIPPED, "No recording."
    elif not enabled():
        call.ai_status, call.ai_error = Call.AIStatus.SKIPPED, "Call analysis isn't set up on this server."
    else:
        try:
            call.transcript = transcribe(call.recording_url)
            if call.transcript:
                result = summarise(call.transcript)
                call.ai_summary = result["summary"]
                call.ai_lead_class = result["lead_class"]
                call.ai_booking_intent = result["booking_intent"]
                if not call.call_reason and result["call_reason"]:
                    call.call_reason = result["call_reason"]
                call.ai_status, call.ai_error = Call.AIStatus.DONE, ""
            else:
                call.ai_status, call.ai_error = Call.AIStatus.SKIPPED, "No speech in the recording."
        except CallAIError as exc:
            logger.warning("Call %s analysis failed: %s", call.pk, exc)
            call.ai_status, call.ai_error = Call.AIStatus.FAILED, str(exc)[:255]
    call.ai_processed_at = timezone.now()
    call.save(update_fields=["transcript", "ai_summary", "ai_lead_class", "ai_booking_intent", "call_reason",
                             "ai_status", "ai_error", "ai_processed_at", "updated_at"])
    return call
