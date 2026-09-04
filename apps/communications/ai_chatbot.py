"""
Interactive 24x7 assistant. Every branch below is a scripted flow that
reads real tenant data (Doctor/Slot/Hospital) and, for bookings, calls the
same `book_appointment` service the front-desk UI uses, so a confirmed
booking here is a real Appointment row, not a canned success message.

The "classify_free_text" action is the one exception: it hands the
patient's typed message to a self-hosted Ollama model (see
apps.communications.llm_router) purely to pick which of the scripted
branches above applies — the model never writes anything a patient sees.
See `AIChatbotView` for how `hospital` gets resolved and threaded in.
"""
from typing import Any, Dict, List, Optional

from django.db.models import Q
from django.utils import timezone

MAIN_OPTIONS = [
    {"id": "book_opd", "label": "📅 Book OPD Appointment"},
    {"id": "view_doctors", "label": "👨‍⚕️ View Doctors & Departments"},
    {"id": "hospital_location", "label": "📍 Hospital Address & Location"},
    {"id": "emergency", "label": "🚨 Emergency Contact"},
    {"id": "lab_reports", "label": "🧪 Lab Reports & Test Info"},
]

_BACK_TO_MENU = {"id": "main_menu", "label": "⬅️ Back to Main Menu"}


def _no_hospital_reply() -> Dict[str, Any]:
    return {
        "text": "I can't look up live hospital details right now — please contact the front desk directly.",
        "options": [_BACK_TO_MENU],
        "step": "main_menu",
    }


def _doctor_label(doctor) -> str:
    extra = doctor.speciality or (doctor.department.name if doctor.department_id else "")
    return f"{doctor}" + (f" ({extra})" if extra else "")


def _available_slots(hospital, doctor, limit: int = 5):
    from apps.appointments.models import Appointment, Slot

    today = timezone.localdate()
    return (
        Slot.objects.filter(hospital=hospital, doctor=doctor, date__gte=today, is_blocked=False)
        .filter(
            Q(appointment__isnull=True)
            | Q(appointment__status__in=[Appointment.Status.CANCELLED, Appointment.Status.RESCHEDULED])
        )
        .order_by("date", "start_time")[:limit]
    )


def process_interactive_chat_action(
    action: str,
    payload: Optional[Dict[str, Any]] = None,
    preferred_language: str = "en",
    hospital=None,
) -> Dict[str, Any]:
    """Processes a button click (or a booking-form submit) and returns the
    next message + options for the widget to render."""
    from apps.appointments.models import Appointment, Doctor, Slot
    from apps.appointments.services import SlotUnavailable, book_appointment
    from apps.patients.models import Patient

    payload = payload or {}

    if action in ("main_menu", "start", "welcome"):
        if preferred_language == "mr":
            text = "नमस्कार! मी पॉलिनेक्सस HMS बॉट आहे, तुमच्या मदतीसाठी २४x७ उपलब्ध आहे. कृपया खालीलपैकी एक पर्याय निवडा:"
        elif preferred_language == "hi":
            text = "नमस्ते! मैं पॉलिनेक्सस HMS बॉट हूं, आपकी मदद के लिए 24x7 उपलब्ध हूं। कृपया नीचे दिए गए विकल्पों में से चुनें:"
        else:
            text = "Hello! I'm Polynexus HMS Bot, here to help 24x7. Please select an option below:"
        return {"text": text, "options": MAIN_OPTIONS, "step": "main_menu"}

    if action == "book_opd":
        if hospital is None:
            return _no_hospital_reply()
        doctors = list(Doctor.objects.filter(hospital=hospital, is_active=True).select_related("department"))
        if not doctors:
            return {
                "text": "No doctors are configured for OPD booking yet — please contact the front desk directly.",
                "options": [_BACK_TO_MENU],
                "step": "book_opd",
            }
        return {
            "text": "Please select the doctor or specialty you wish to consult:",
            "options": [{"id": f"select_doc_{d.id}", "label": _doctor_label(d)} for d in doctors] + [_BACK_TO_MENU],
            "step": "select_doctor",
        }

    if action.startswith("select_doc_"):
        if hospital is None:
            return _no_hospital_reply()
        doc_id = action.replace("select_doc_", "")
        doctor = Doctor.objects.filter(hospital=hospital, pk=doc_id, is_active=True).first()
        if doctor is None:
            return {
                "text": "That doctor is no longer available.",
                "options": [{"id": "book_opd", "label": "⬅️ Choose Again"}],
                "step": "book_opd",
            }
        slots = list(_available_slots(hospital, doctor))
        if not slots:
            return {
                "text": f"{doctor} has no open OPD slots in the coming days. Please check back later or contact the front desk.",
                "options": [{"id": "book_opd", "label": "⬅️ Choose Different Doctor"}, _BACK_TO_MENU],
                "step": "select_doctor",
            }
        return {
            "text": f"{_doctor_label(doctor)}\n\nPlease choose an available slot:",
            "options": [
                {"id": f"pick_slot_{s.id}", "label": f"{s.date.strftime('%d %b')}, {s.start_time.strftime('%I:%M %p')}"}
                for s in slots
            ] + [{"id": "book_opd", "label": "⬅️ Choose Different Doctor"}],
            "step": "select_slot",
        }

    if action.startswith("pick_slot_"):
        if hospital is None:
            return _no_hospital_reply()
        slot_id = action.replace("pick_slot_", "")
        slot = Slot.objects.filter(hospital=hospital, pk=slot_id).select_related("doctor").first()
        if slot is None or slot.is_blocked or slot.is_booked:
            return {
                "text": "Sorry, that slot is no longer available. Please pick another.",
                "options": [{"id": "book_opd", "label": "⬅️ Choose Again"}],
                "step": "book_opd",
            }
        return {
            "text": (
                f"{_doctor_label(slot.doctor)} — {slot.date.strftime('%d %b')} at {slot.start_time.strftime('%I:%M %p')}\n\n"
                "Please share the patient's name and mobile number to confirm this booking."
            ),
            "options": [],
            "step": "collect_details",
            "requires_input": ["name", "mobile"],
            "pending_slot_id": slot.id,
        }

    if action == "submit_booking":
        if hospital is None:
            return _no_hospital_reply()
        slot_id = payload.get("slot_id")
        name = str(payload.get("name") or "").strip()
        mobile = str(payload.get("mobile") or "").strip()

        if not slot_id:
            return {"text": "Let's start over — please pick a doctor first.", "options": [{"id": "book_opd", "label": "📅 Book OPD Appointment"}], "step": "book_opd"}
        if not mobile:
            return {
                "text": "I need at least a mobile number to confirm the booking.",
                "options": [],
                "step": "collect_details",
                "requires_input": ["name", "mobile"],
                "pending_slot_id": slot_id,
            }

        slot = Slot.objects.filter(hospital=hospital, pk=slot_id).select_related("doctor").first()
        if slot is None:
            return {"text": "That slot could not be found — let's start again.", "options": [{"id": "book_opd", "label": "📅 Book OPD Appointment"}], "step": "book_opd"}

        patient = Patient.objects.filter(hospital=hospital, mobile=mobile).first()
        if patient is None:
            patient = Patient.objects.create(
                hospital=hospital,
                first_name=name or "Patient",
                mobile=mobile,
                preferred_language=preferred_language if preferred_language in ("en", "hi", "mr") else "mr",
            )

        try:
            appointment = book_appointment(
                patient=patient, slot=slot, source=Appointment.Source.WHATSAPP,
                reason="Booked via Polynexus HMS Bot",
            )
        except SlotUnavailable:
            return {
                "text": "Sorry — that slot was just booked by someone else. Please pick another slot.",
                "options": [{"id": f"select_doc_{slot.doctor_id}", "label": "⬅️ Pick Another Slot"}],
                "step": "select_slot",
            }

        return {
            "text": (
                "✅ Appointment confirmed!\n\n"
                f"• Patient: {patient.full_name}\n"
                f"• Doctor: {appointment.doctor}\n"
                f"• Date: {appointment.slot.date.strftime('%d %b %Y')}\n"
                f"• Time: {appointment.slot.start_time.strftime('%I:%M %p')}\n"
                f"• Reference: {appointment.registration_token[:8].upper()}\n\n"
                "This is now on record with the front desk."
            ),
            "options": [{"id": "book_opd", "label": "📅 Book Another Appointment"}, {"id": "main_menu", "label": "🏠 Main Menu"}],
            "step": "confirmed",
            "confirmed_details": {
                "doctor": str(appointment.doctor),
                "date": appointment.slot.date.isoformat(),
                "time": appointment.slot.start_time.isoformat(),
                "appointment_id": appointment.id,
            },
        }

    if action == "view_doctors":
        if hospital is None:
            return _no_hospital_reply()
        doctors = list(Doctor.objects.filter(hospital=hospital, is_active=True).select_related("department"))
        if not doctors:
            text = "No doctors are configured yet — please contact the front desk directly."
        else:
            text = "👨‍⚕️ OPD Roster:\n\n" + "\n".join(f"• {_doctor_label(d)}" for d in doctors)
            text += "\n\nTap 'Book OPD Appointment' to see live available slots for any of them."
        return {
            "text": text,
            "options": [{"id": "book_opd", "label": "📅 Book OPD Appointment Now"}, _BACK_TO_MENU],
            "step": "view_doctors",
        }

    if action == "hospital_location":
        if hospital is None:
            return _no_hospital_reply()
        location_bits = ", ".join(b for b in [hospital.address, hospital.city, hospital.state] if b)
        text = f"📍 {hospital.name}\n\n• Address: {location_bits or 'Please contact the front desk for full address details.'}"
        return {
            "text": text,
            "options": [{"id": "emergency", "label": "🚨 Emergency Contact"}, _BACK_TO_MENU],
            "step": "location",
        }

    if action == "emergency":
        hospital_name = hospital.name if hospital else "the hospital"
        return {
            "text": (
                f"🚨 For a medical emergency, please call {hospital_name}'s reception directly, "
                "or dial 108 for an ambulance."
            ),
            "options": [_BACK_TO_MENU],
            "step": "emergency",
        }

    if action == "lab_reports":
        return {
            "text": (
                "🧪 For diagnostic/lab report queries — sample collection timings, report pickup, "
                "or digital delivery — please contact the front desk, who can look up your specific test."
            ),
            "options": [_BACK_TO_MENU],
            "step": "lab_reports",
        }

    if action == "classify_free_text":
        # The widget's free-text box (and generate_ai_chat_response below)
        # both land here: an Ollama model only decides WHICH of the actions
        # above best matches what the patient typed — it never generates the
        # reply itself, so the result is exactly as safe as a button click.
        from .llm_router import classify_free_text_intent

        message = str(payload.get("message") or "").strip()
        intent = classify_free_text_intent(message)
        result = process_interactive_chat_action(intent, payload, preferred_language, hospital=hospital)
        if intent == "unclear" and message:
            prefix = "Sorry, I didn't quite catch that — here's what I can help with:\n\n"
            result = {**result, "text": prefix + result["text"]}
        return result

    # Fallback to main menu
    return process_interactive_chat_action("main_menu", payload, preferred_language, hospital=hospital)


def generate_ai_chat_response(
    prompt: str,
    patient_name: str = "Patient",
    preferred_language: str = "en",
    history: Optional[List[Dict[str, str]]] = None,
    hospital=None,
) -> str:
    """Text wrapper for the inbound-webhook auto-reply (apps.communications.
    views.InboundWebhookView) — an inbound WhatsApp/SMS message has no
    buttons to click, so this classifies the free text the same way the
    widget's text box does (see the "classify_free_text" action above) and
    returns just the resulting message body. `history` isn't threaded into
    the classifier: each inbound message gets one independent reply rather
    than a stateful multi-turn conversation — see the module docstring on
    why the model is kept to routing, not conversation, in the first place."""
    res = process_interactive_chat_action(
        "classify_free_text", {"message": prompt}, preferred_language=preferred_language, hospital=hospital,
    )
    return res["text"]
