"""
Free-text understanding for the 24x7 assistant, backed by a self-hosted
Ollama model (see settings.OLLAMA_*). Deliberately scoped to ONE job:
classify what a patient typed into one of the assistant's existing,
hand-written menu actions (apps.communications.ai_chatbot.MAIN_OPTIONS)
plus "unclear". The model never composes the reply a patient actually
sees — process_interactive_chat_action's curated, tested copy still owns
every word of that, so a misbehaving or hallucinating model can misroute
a request but can never invent a doctor, a slot, a price, or medical
advice. Any failure (network, timeout, malformed output) degrades to
"unclear", which callers treat exactly like today's default: show the
main menu.
"""
import json
import logging
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Keep this in sync with apps.communications.ai_chatbot.MAIN_OPTIONS' ids —
# "unclear" is the safe fallback, not a real action.
KNOWN_INTENTS = ["book_opd", "view_doctors", "hospital_location", "emergency", "lab_reports", "unclear"]

_SYSTEM_PROMPT = (
    "You are an intent router for a hospital's patient-facing assistant. "
    "You are NOT a medical assistant: never diagnose, never suggest treatment, "
    "never answer a clinical question yourself — only classify the patient's "
    "message into exactly one of these categories and respond with nothing "
    "but a JSON object of the form {\"intent\": \"<category>\"}.\n\n"
    "Categories:\n"
    "- book_opd: wants to book, reschedule, or ask about a doctor appointment\n"
    "- view_doctors: wants to know which doctors/specialties/departments are available\n"
    "- hospital_location: asks for the hospital's address, directions, or contact number\n"
    "- emergency: describes symptoms, pain, an injury, or any urgent/emergency situation\n"
    "- lab_reports: asks about lab/diagnostic test reports, sample collection, or results\n"
    "- unclear: anything else, including small talk, or a question you cannot confidently place above\n\n"
    "When in doubt between emergency and something else, choose emergency — it is always safe to "
    "route a patient to the hospital's emergency contact."
)


def classify_free_text_intent(message: str) -> str:
    """Returns one of KNOWN_INTENTS. Never raises — any problem reaching or
    parsing the model's response is logged and treated as "unclear"."""
    message = (message or "").strip()
    if not message:
        return "unclear"

    try:
        response = requests.post(
            f"{settings.OLLAMA_BASE_URL}/api/chat",
            json={
                "model": settings.OLLAMA_MODEL,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": message[:2000]},
                ],
            },
            timeout=settings.OLLAMA_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        intent = json.loads(content).get("intent")
    except (requests.RequestException, KeyError, ValueError) as exc:
        logger.warning("Ollama intent classification failed, falling back to unclear: %s", exc)
        return "unclear"

    return intent if intent in KNOWN_INTENTS else "unclear"


def ollama_is_reachable(timeout: Optional[int] = 3) -> bool:
    """Cheap health check — e.g. for an admin/status page. Not used on the
    hot path (classify_free_text_intent already degrades gracefully on its
    own), just for visibility into whether the VM is up."""
    try:
        response = requests.get(f"{settings.OLLAMA_BASE_URL}/api/tags", timeout=timeout)
        return response.ok
    except requests.RequestException:
        return False
