"""
Executes the DPDP data-principal rights (Part A #11) once a
DataRightsRequest has been verified — this module is where the actual
"do the thing" logic lives, kept separate from the request-tracking model
so DataRightsRequestViewSet stays a thin HTTP layer over it.
"""
from django.utils import timezone

from apps.patients.models import Document, Prescription


def collect_patient_data(patient) -> dict:
    """Assembles a data-principal's "right to access" export.

    Covers the patient's own record plus the models most directly
    identifying/clinical in this codebase (documents, prescriptions,
    appointments) — not yet every one of the clinical modules (OPD/IPD/
    laboratory/radiology/pharmacy/etc.). Extending coverage to those is
    the same pattern: pull the patient's related rows, decrypt (already
    transparent via apps.core.fields on read), shape into a dict. Listed
    here explicitly rather than silently claiming full-platform coverage."""
    from apps.appointments.models import Appointment

    return {
        "patient": {
            "id": str(patient.id),
            "first_name": patient.first_name,
            "last_name": patient.last_name,
            "date_of_birth": patient.date_of_birth.isoformat() if patient.date_of_birth else None,
            "gender": patient.gender,
            "mobile": patient.mobile,
            "alternate_mobile": patient.alternate_mobile,
            "email": patient.email,
            "address": patient.address,
            "city": patient.city,
            "national_id_type": patient.national_id_type,
            "national_id_number": patient.national_id_number,
            "insurance_provider": patient.insurance_provider,
            "insurance_policy_number": patient.insurance_policy_number,
            "preferred_language": patient.preferred_language,
        },
        "documents": [
            {"id": str(d.id), "category": d.category, "title": d.title, "notes": d.notes, "created_at": d.created_at.isoformat()}
            for d in Document.objects.filter(patient=patient)
        ],
        "prescriptions": [
            {
                "id": str(p.id), "diagnosis": p.diagnosis, "symptoms": p.symptoms,
                "medications": p.medications, "lab_orders": p.lab_orders,
                "notes": p.notes, "created_at": p.created_at.isoformat(),
            }
            for p in Prescription.objects.filter(patient=patient)
        ],
        "appointments": [
            {
                "id": str(a.id), "status": a.status, "reason": a.reason,
                "doctor": a.doctor.name if a.doctor_id else None,
                "date": a.slot.date.isoformat() if a.slot_id else None,
            }
            for a in Appointment.objects.filter(patient=patient)
        ],
        "generated_at": timezone.now().isoformat(),
    }


def complete_erasure(data_rights_request, *, actor):
    """Executes the "right to erasure" — soft-deletes the patient (Part A
    #1: never a hard delete here; apps.automation.tasks.
    purge_expired_soft_deleted_records removes it for real after the
    grace period, completing the request in two steps rather than one
    irreversible one)."""
    patient = data_rights_request.patient
    patient.delete(actor=actor, reason=f"DPDP erasure request #{data_rights_request.pk}")
