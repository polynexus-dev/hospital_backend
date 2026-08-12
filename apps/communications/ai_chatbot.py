from typing import Any, Dict, List, Optional

DOCTORS = [
    {
        "id": "doc_kulkarni",
        "name": "Dr. Ramesh Kulkarni",
        "speciality": "General Medicine",
        "timings": "Mon, Wed, Fri (9:00 AM - 1:00 PM)",
    },
    {
        "id": "doc_joshi",
        "name": "Dr. Anjali Joshi",
        "speciality": "Cardiology",
        "timings": "Mon, Wed, Fri (9:00 AM - 1:00 PM)",
    },
    {
        "id": "doc_sharma",
        "name": "Dr. Rajesh Sharma",
        "speciality": "Orthopedics",
        "timings": "Mon, Wed, Fri (9:00 AM - 1:00 PM)",
    },
]

MAIN_OPTIONS = [
    {"id": "book_opd", "label": "📅 Book OPD Appointment"},
    {"id": "view_doctors", "label": "👨‍⚕️ View Doctors & Timings"},
    {"id": "hospital_location", "label": "📍 Hospital Address & Location"},
    {"id": "emergency", "label": "🚨 24x7 Emergency Helpline"},
    {"id": "lab_reports", "label": "🧪 Lab Reports & Test Info"},
]


def process_interactive_chat_action(
    action: str,
    payload: Optional[Dict[str, Any]] = None,
    preferred_language: str = "en",
) -> Dict[str, Any]:
    """Processes predefined option clicks and returns structured message + option buttons."""
    payload = payload or {}

    # Initial Welcome or Main Menu
    if action in ["main_menu", "start", "welcome"]:
        if preferred_language == "mr":
            text = "नमस्कार! पॉलिनेक्सस हॉस्पिटल २४x७ सहाय्यकामध्ये आपले स्वागत आहे. कृपया खालीलपैकी एक पर्याय निवडा:"
        elif preferred_language == "hi":
            text = "नमस्ते! पॉलीनेक्सस अस्पताल 24x7 सहायक में आपका स्वागत है। कृपया नीचे दिए गए विकल्पों में से चुनें:"
        else:
            text = "Hello! Welcome to Polynexus Hospital 24x7 Assistant. Please select an option below:"

        return {
            "text": text,
            "options": MAIN_OPTIONS,
            "step": "main_menu",
        }

    # 1. Book OPD
    if action == "book_opd":
        return {
            "text": "Please select the doctor or specialty you wish to consult:",
            "options": [
                {"id": f"select_doc_{d['id']}", "label": f"{d['name']} ({d['speciality']})"}
                for d in DOCTORS
            ] + [{"id": "main_menu", "label": "⬅️ Back to Main Menu"}],
            "step": "select_doctor",
        }

    # 2. Select Doctor
    if action.startswith("select_doc_"):
        doc_id = action.replace("select_doc_", "")
        selected_doc = next((d for d in DOCTORS if d["id"] == doc_id), DOCTORS[0])
        return {
            "text": (
                f"Selected: {selected_doc['name']} ({selected_doc['speciality']})\n"
                f"OPD Hours: {selected_doc['timings']}\n\n"
                "Please choose your preferred consultation time slot:"
            ),
            "options": [
                {"id": f"confirm_slot_morning_{doc_id}", "label": "🌅 Morning Slot (9:00 AM - 11:00 AM)"},
                {"id": f"confirm_slot_late_{doc_id}", "label": "☀️ Late Morning Slot (11:00 AM - 1:00 PM)"},
                {"id": "book_opd", "label": "⬅️ Choose Different Doctor"},
            ],
            "step": "select_slot",
            "selected_doctor": selected_doc,
        }

    # 3. Confirm Slot
    if action.startswith("confirm_slot_"):
        parts = action.split("_")
        slot_type = parts[2]
        doc_id = "_".join(parts[3:])
        selected_doc = next((d for d in DOCTORS if d["id"] == doc_id), DOCTORS[0])
        time_label = "9:00 AM - 11:00 AM" if slot_type == "morning" else "11:00 AM - 1:00 PM"

        return {
            "text": (
                "✅ OPD Consultation Reserved Successfully!\n\n"
                f"• Doctor: {selected_doc['name']}\n"
                f"• Department: {selected_doc['speciality']}\n"
                f"• Preferred Time: {time_label}\n"
                "• Hospital Location: OPD Block, Baner Road, Pune\n\n"
                "Our front desk will send a WhatsApp confirmation reminder shortly!"
            ),
            "options": [
                {"id": "book_opd", "label": "📅 Book Another Appointment"},
                {"id": "main_menu", "label": "🏠 Return to Main Menu"},
            ],
            "step": "confirmed",
            "confirmed_details": {
                "doctor": selected_doc["name"],
                "speciality": selected_doc["speciality"],
                "time": time_label,
            },
        }

    # 4. View Doctors
    if action == "view_doctors":
        doc_text = "👨‍⚕️ Polynexus Hospital Specialist OPD Roster:\n\n" + "\n\n".join([
            f"• {d['name']}\n  Speciality: {d['speciality']}\n  Timings: {d['timings']}"
            for d in DOCTORS
        ])
        return {
            "text": doc_text,
            "options": [
                {"id": "book_opd", "label": "📅 Book OPD Appointment Now"},
                {"id": "main_menu", "label": "🏠 Main Menu"},
            ],
            "step": "view_doctors",
        }

    # 5. Hospital Location
    if action == "hospital_location":
        return {
            "text": (
                "📍 Polynexus Hospital Address & Directions:\n\n"
                "• Address: Baner Road, Near Baner Petrol Pump, Pune, Maharashtra 411045\n"
                "• Landmark: Opposite Regent Plaza\n"
                "• OPD Registration Hours: Mon-Sat 8:00 AM - 8:00 PM\n"
                "• Emergency Desk: Open 24 Hours (24x7)"
            ),
            "options": [
                {"id": "emergency", "label": "🚨 24x7 Emergency Contact"},
                {"id": "main_menu", "label": "🏠 Main Menu"},
            ],
            "step": "location",
        }

    # 6. Emergency
    if action == "emergency":
        return {
            "text": (
                "🚨 24x7 Emergency & Trauma Helpline:\n\n"
                "• Emergency Hotline: +91 98765 43210\n"
                "• Ambulance Service: 108 / +91 98765 43211\n"
                "• Address: Emergency Entrance, Polynexus Hospital, Baner Road, Pune."
            ),
            "options": [
                {"id": "main_menu", "label": "🏠 Return to Main Menu"},
            ],
            "step": "emergency",
        }

    # 7. Lab Reports
    if action == "lab_reports":
        return {
            "text": (
                "🧪 Diagnostic & Pathology Lab Services:\n\n"
                "• Sample Collection: Mon-Sat 7:30 AM - 6:00 PM\n"
                "• Report Pickup Desk: OPD Counter #3\n"
                "• Digital Reports: Sent automatically on WhatsApp & Patient Vault."
            ),
            "options": [
                {"id": "main_menu", "label": "🏠 Return to Main Menu"},
            ],
            "step": "lab_reports",
        }

    # Fallback to main menu
    return process_interactive_chat_action("main_menu", payload, preferred_language)


def generate_ai_chat_response(
    prompt: str,
    patient_name: str = "Patient",
    preferred_language: str = "en",
    history: Optional[List[Dict[str, str]]] = None,
) -> str:
    """Legacy text wrapper returning text string."""
    res = process_interactive_chat_action("main_menu", preferred_language=preferred_language)
    return res["text"]
