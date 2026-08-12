from typing import Any, Dict, List
from apps.patients.models import Patient
from apps.appointments.models import Appointment


def patient_to_fhir_resource(patient: Patient) -> Dict[str, Any]:
    """Converts a Patient instance into an HL7 FHIR R4 Patient Resource."""
    names = []
    if patient.first_name or patient.last_name:
        name_obj: Dict[str, Any] = {
            "use": "official",
            "text": f"{patient.first_name} {patient.last_name}".strip(),
        }
        if patient.last_name:
            name_obj["family"] = patient.last_name
        if patient.first_name:
            name_obj["given"] = [patient.first_name]
        names.append(name_obj)

    telecom = []
    if patient.mobile:
        telecom.append({"system": "phone", "value": patient.mobile, "use": "mobile"})
    if patient.email:
        telecom.append({"system": "email", "value": patient.email})

    gender_map = {
        "male": "male",
        "female": "female",
        "other": "other",
        "undisclosed": "unknown",
    }
    gender = gender_map.get(patient.gender.lower(), "unknown") if patient.gender else "unknown"

    address = []
    if patient.address or patient.city:
        addr_obj: Dict[str, Any] = {"use": "home"}
        if patient.address:
            addr_obj["line"] = [patient.address]
        if patient.city:
            addr_obj["city"] = patient.city
        address.append(addr_obj)

    resource: Dict[str, Any] = {
        "resourceType": "Patient",
        "id": str(patient.id),
        "active": patient.is_active,
        "name": names,
        "telecom": telecom,
        "gender": gender,
        "address": address,
        "communication": [
            {
                "language": {
                    "coding": [
                        {
                            "system": "urn:ietf:bcp:47",
                            "code": patient.preferred_language or "en",
                            "display": "Marathi" if patient.preferred_language == "mr" else "Hindi" if patient.preferred_language == "hi" else "English",
                        }
                    ]
                }
            }
        ],
    }

    if patient.national_id_number:
        resource["identifier"] = [
            {
                "type": {"text": patient.national_id_type or "National ID"},
                "value": patient.national_id_number,
            }
        ]

    return resource


def appointment_to_fhir_resource(appointment: Appointment) -> Dict[str, Any]:
    """Converts an Appointment instance into an HL7 FHIR R4 Appointment Resource."""
    status_map = {
        "booked": "booked",
        "confirmed": "booked",
        "checked_in": "arrived",
        "in_consult": "fulfilled",
        "completed": "fulfilled",
        "no_show": "noshow",
        "cancelled": "cancelled",
        "rescheduled": "cancelled",
    }
    fhir_status = status_map.get(appointment.status, "booked")

    participants = [
        {
            "actor": {
                "reference": f"Patient/{appointment.patient_id}",
                "display": appointment.patient.full_name or f"{appointment.patient.first_name} {appointment.patient.last_name}",
            },
            "status": "accepted",
        },
        {
            "actor": {
                "reference": f"Practitioner/{appointment.doctor_id}",
                "display": appointment.doctor.name,
            },
            "status": "accepted",
        },
    ]

    start_iso = f"{appointment.slot.date}T{appointment.slot.start_time}"
    end_iso = f"{appointment.slot.date}T{appointment.slot.end_time}"

    return {
        "resourceType": "Appointment",
        "id": str(appointment.id),
        "status": fhir_status,
        "description": appointment.reason or "OPD Consultation",
        "start": start_iso,
        "end": end_iso,
        "created": appointment.created_at.isoformat() if appointment.created_at else None,
        "participant": participants,
    }


def to_fhir_bundle(resources: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Wraps a list of FHIR resources into a FHIR R4 Bundle resource."""
    entries = []
    for r in resources:
        entries.append({
            "fullUrl": f"urn:uuid:{r.get('id')}",
            "resource": r,
        })

    return {
        "resourceType": "Bundle",
        "type": "collection",
        "total": len(resources),
        "entry": entries,
    }
