"""NHCX pre-authorisation: the pre-auth form as a FHIR R4 claim bundle
(NRCeS NHCX "ClaimBundle" shape — a Claim with use = "preauthorization",
plus the Patient, Coverage, provider and payer Organizations and the
treating Practitioner it references), what's still missing before it can
be sent, and submission through the NHCX gateway.

Submission goes through apps.abdm.gateway.get_nhcx_gateway(); until the
hospital is onboarded on NHCX the gateway is a stub, so the attempt is
logged on the NHCX transaction log with the reason and nothing leaves.
"""
from decimal import Decimal

from django.utils import timezone

from .models import NHCXProfile

ROOM_LABELS = {"general": "General ward", "semi_private": "Semi-private", "private": "Private room", "icu": "ICU", "ventilator": "ICU with ventilator"}


def profile_for(hospital_id):
    return NHCXProfile.objects.filter(hospital_id=hospital_id).first() or NHCXProfile(hospital_id=hospital_id)


def missing_fields(pa):
    """What the payer needs that the pre-auth (or set-up) doesn't have yet."""
    prof = profile_for(pa.hospital_id)
    checks = [
        (prof.participant_code, "Hospital NHCX participant code (Insurance › NHCX set-up)"),
        (prof.hfr_id or prof.rohini_id, "Hospital HFR or ROHINI ID"),
        (pa.tpa_company.nhcx_participant_code, f"NHCX participant code for {pa.tpa_company.name}"),
        (pa.policy_number, "Policy number"),
        (pa.member_id, "Member ID"),
        (pa.diagnosis_code, "Diagnosis (ICD-10 code)"),
        (pa.procedure_name, "Proposed procedure / line of treatment"),
        (pa.treating_doctor_id, "Treating doctor"),
        (pa.expected_admission_date, "Expected admission date"),
        (pa.cost_breakdown, "Estimated cost breakdown"),
        (pa.clinical_summary, "Clinical summary"),
    ]
    return [label for value, label in checks if not value]


def _amount(value):
    return {"value": float(Decimal(str(value or 0))), "currency": "INR"}


def build_bundle(pa):
    hospital, patient, payer = pa.hospital, pa.patient, pa.tpa_company
    prof = profile_for(pa.hospital_id)
    ids = {"patient": f"Patient/{patient.pk}", "provider": f"Organization/provider-{prof.participant_code or hospital.pk}",
           "payer": f"Organization/payer-{payer.nhcx_participant_code or payer.pk}", "coverage": f"Coverage/{pa.pk}",
           "practitioner": f"Practitioner/{pa.treating_doctor_id}" if pa.treating_doctor_id else None, "claim": f"Claim/preauth-{pa.pk}"}
    lines = [l for l in (pa.cost_breakdown or []) if isinstance(l, dict)]
    total = sum((Decimal(str(l.get("amount") or 0)) for l in lines), Decimal("0")) or Decimal(pa.claim_amount or 0)

    provider_ids = [{"system": "https://facility.ndhm.gov.in", "value": prof.hfr_id}] if prof.hfr_id else []
    if prof.rohini_id:
        provider_ids.append({"system": "https://rohini.iib.gov.in", "value": prof.rohini_id})
    resources = [
        {"resourceType": "Organization", "id": ids["provider"].split("/")[1], "name": hospital.name, "identifier": provider_ids,
         "address": [{"text": hospital.address, "city": hospital.city, "state": hospital.state, "country": "IN"}] if hospital.address else []},
        {"resourceType": "Organization", "id": ids["payer"].split("/")[1], "name": payer.name,
         "identifier": [{"system": "https://hcx.abdm.gov.in/participant", "value": payer.nhcx_participant_code}] if payer.nhcx_participant_code else []},
        {"resourceType": "Patient", "id": str(patient.pk), "name": [{"text": patient.full_name}], "gender": patient.gender if patient.gender in ("male", "female", "other") else "unknown",
         "birthDate": patient.date_of_birth.isoformat() if patient.date_of_birth else None,
         "identifier": [{"system": "https://hospital.local/uhid", "value": patient.uhid}] if patient.uhid else []},
        {"resourceType": "Coverage", "id": str(pa.pk), "status": "active", "subscriberId": pa.member_id, "beneficiary": {"reference": ids["patient"]},
         "payor": [{"reference": ids["payer"]}], "identifier": [{"system": "https://insurer.policy", "value": pa.policy_number}]},
    ]
    if pa.treating_doctor_id:
        resources.append({"resourceType": "Practitioner", "id": str(pa.treating_doctor_id), "name": [{"text": f"Dr. {pa.treating_doctor.name}"}],
                          "qualification": [{"code": {"text": pa.treating_doctor.speciality}}] if pa.treating_doctor.speciality else []})
    claim = {
        "resourceType": "Claim", "id": ids["claim"].split("/")[1], "status": "active", "use": "preauthorization",
        "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claim-type", "code": "institutional"}]},
        "created": timezone.now().isoformat(), "patient": {"reference": ids["patient"]},
        "provider": {"reference": ids["provider"]}, "insurer": {"reference": ids["payer"]},
        "priority": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/processpriority", "code": "stat" if pa.admission_type == "emergency" else "normal"}]},
        "insurance": [{"sequence": 1, "focal": True, "coverage": {"reference": ids["coverage"]}}],
        "diagnosis": [{"sequence": 1, "diagnosisCodeableConcept": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10", "code": pa.diagnosis_code, "display": pa.diagnosis}],
                                                                    "text": pa.diagnosis}}] if pa.diagnosis_code or pa.diagnosis else [],
        "procedure": [{"sequence": 1, "procedureCodeableConcept": {"text": pa.procedure_name, **({"coding": [{"code": pa.procedure_code}]} if pa.procedure_code else {})}}] if pa.procedure_name else [],
        "supportingInfo": [x for x in (
            {"sequence": 1, "category": {"text": "clinical-summary"}, "valueString": pa.clinical_summary} if pa.clinical_summary else None,
            {"sequence": 2, "category": {"text": "expected-admission"}, "timingDate": pa.expected_admission_date.isoformat()} if pa.expected_admission_date else None,
            {"sequence": 3, "category": {"text": "expected-length-of-stay"}, "valueQuantity": {"value": pa.expected_stay_days, "unit": "days"}} if pa.expected_stay_days else None,
            {"sequence": 4, "category": {"text": "room-category"}, "valueString": ROOM_LABELS.get(pa.room_category, pa.room_category)} if pa.room_category else None,
        ) if x],
        "item": [{"sequence": n, "productOrService": {"text": l.get("description") or l.get("category") or "Charges"},
                  "category": {"text": l.get("category", "")}, "unitPrice": _amount(l.get("amount")), "net": _amount(l.get("amount"))}
                 for n, l in enumerate(lines, start=1)],
        "total": _amount(total),
    }
    if ids["practitioner"]:
        claim["careTeam"] = [{"sequence": 1, "provider": {"reference": ids["practitioner"]}, "role": {"text": "primary"}}]
    resources.insert(0, claim)
    return {
        "resourceType": "Bundle", "id": f"preauth-{pa.pk}", "type": "collection", "timestamp": timezone.now().isoformat(),
        "meta": {"profile": ["https://nrces.in/ndhm/fhir/r4/StructureDefinition/ClaimBundle"]},
        "entry": [{"fullUrl": f"{r['resourceType']}/{r['id']}", "resource": r} for r in resources],
    }


def submit(pa, user):
    """Sends the pre-auth over NHCX. Returns (transaction, error or None)."""
    from apps.abdm.gateway import GatewayNotConfigured, get_nhcx_gateway
    from apps.abdm.models import NHCXTransaction

    tx = NHCXTransaction.objects.create(hospital_id=pa.hospital_id, preauth_request=pa, transaction_type=NHCXTransaction.TransactionType.PRE_AUTH, initiated_by=user)
    try:
        result = get_nhcx_gateway().submit_transaction(transaction_type="pre_auth", payload=build_bundle(pa))
    except GatewayNotConfigured as exc:
        tx.status, tx.gateway_response_summary = NHCXTransaction.Status.ERROR, str(exc)[:255]
        tx.save(update_fields=["status", "gateway_response_summary", "updated_at"])
        return tx, str(exc)
    tx.nhcx_transaction_id = result.get("nhcx_transaction_id", "")
    tx.status = result.get("status", NHCXTransaction.Status.PENDING)
    tx.save(update_fields=["nhcx_transaction_id", "status", "updated_at"])
    return tx, None
