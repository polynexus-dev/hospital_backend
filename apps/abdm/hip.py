"""
This hospital as Health Information Provider (ABDM milestone M2).

  discovery      the patient's PHR app asks "what do you hold for me?" —
                 matched on their ABHA address, or verified mobile plus
                 name, gender and year of birth; we answer with the records
                 not yet linked, grouped by HI type
  link           they pick records; we send an OTP to their registered
                 mobile and link once they confirm it. Staff can also link
                 a visit's records from the hospital side (link token flow)
  consent        their Consent Manager tells us what another provider may
                 pull — the artefact is the only thing that authorises it
  data flow      the HIU asks for data under that artefact with its public
                 key; we build the FHIR documents, encrypt them for that key
                 (apps.abdm.fidelius), push them, and report what was sent

Every function here takes the gateway callback's payload and answers by
calling the gateway; none of them is reachable without a verified gateway
token (apps.abdm.callbacks).
"""
import json
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import fidelius, records
from .gateway import get_abdm_gateway
from .models import AbhaLink, CareContext, ConsentArtefact, DataTransfer, LinkRequest

logger = logging.getLogger(__name__)

MEDIA_FHIR = "application/fhir+json"
OTP_PURPOSE = "abdm_link"


def _reply(callback, endpoint, body, *, hip_id, headers=None):
    body = {**body, "response": {"requestId": callback.request_id}}
    get_abdm_gateway().send(endpoint, body, hip_id=hip_id, headers=headers)


def _error(code, message):
    return {"error": {"code": code, "message": message}}


def _dt(value):
    parsed = parse_datetime(str(value or ""))
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _iso(dt):
    return dt.astimezone(timezone.get_current_timezone()).isoformat(timespec="milliseconds") if dt else None


# --- Discovery ------------------------------------------------------------

def _identifier(patient_block, kind):
    for item in patient_block.get("verifiedIdentifiers") or []:
        if str(item.get("type", "")).upper() == kind:
            return str(item.get("value", ""))
    return ""


def _gender_matches(ours, theirs):
    return bool(ours) and str(theirs or "").strip().upper()[:1] == ours[:1].upper()


def find_patient(hospital, patient_block):
    """(patient, abha_link, matched_by) or (None, None, []). The ABHA address
    first; otherwise exactly one patient with that verified mobile, name,
    gender and year of birth — never a guess between several."""
    from apps.core.encryption import blind_index, normalize_phone
    from apps.patients.models import Patient

    address = str(patient_block.get("id") or patient_block.get("abhaAddress") or "").strip()
    if address:
        link = AbhaLink.objects.filter(hospital=hospital, abha_address_hash=AbhaLink.address_hash(address), status=AbhaLink.Status.LINKED).select_related("patient").first()
        if link:
            return link.patient, link, ["ABHA_ADDRESS"]

    mobile = normalize_phone(_identifier(patient_block, "MOBILE"))
    if not mobile:
        return None, None, []
    name = str(patient_block.get("name") or "").strip().lower()
    year = patient_block.get("yearOfBirth")
    candidates = [
        p for p in Patient.objects.filter(hospital=hospital, mobile_hash=blind_index(mobile))
        if _gender_matches(p.gender, patient_block.get("gender"))
        and (not year or p.birth_year == int(year))
        and name and str(p.first_name).strip().lower() in name
    ]
    if len(candidates) != 1:
        return None, None, []
    patient = candidates[0]
    link, _ = AbhaLink.objects.get_or_create(
        hospital=hospital, patient=patient,
        defaults={"verification_method": AbhaLink.VerificationMethod.EXISTING_ABHA, "status": AbhaLink.Status.PENDING},
    )
    if address and link.status != AbhaLink.Status.LINKED:
        # ABDM verified who is asking; the demographic match ties them to this record.
        link.abha_address = address
        link.abha_number = _identifier(patient_block, "ABHA_NUMBER") or link.abha_number
        link.status = AbhaLink.Status.LINKED
        link.linked_at = timezone.now()
        link.save()
    return patient, link, ["MOBILE", "NAME", "GENDER", "YEAR_OF_BIRTH"]


def _grouped(patient, pairs):
    groups = {}
    for rk, record in pairs:
        group = groups.setdefault(rk.hi_type, [])
        group.append({"referenceNumber": records.reference(rk.kind, record.pk), "display": records.display(rk, record)})
    return [
        {"referenceNumber": patient.uhid or str(patient.pk), "display": patient.full_name, "careContexts": contexts, "hiType": hi_type, "count": len(contexts)}
        for hi_type, contexts in groups.items()
    ]


def handle_discover(callback, payload, facility):
    hospital = facility.hospital
    patient, link, matched_by = find_patient(hospital, payload.get("patient") or {})
    body = {"transactionId": payload.get("transactionId")}
    if patient is None:
        body.update(_error("ABDM-1010", "No matching patient record found."))
    else:
        linked = set(CareContext.objects.filter(hospital=hospital, patient=patient, status=CareContext.Status.LINKED).values_list("reference", flat=True))
        unlinked = [(rk, r) for rk, r in records.care_contexts_for(patient) if records.reference(rk.kind, r.pk) not in linked]
        body.update({"patient": _grouped(patient, unlinked), "matchedBy": matched_by})
    _reply(callback, "on_discover", body, hip_id=facility.hip_id)


# --- Patient-initiated linking -------------------------------------------

def handle_link_init(callback, payload, facility):
    from apps.patients.registration import issue_otp

    hospital = facility.hospital
    address = str(payload.get("abhaAddress") or "")
    link = AbhaLink.objects.filter(hospital=hospital, abha_address_hash=AbhaLink.address_hash(address), status=AbhaLink.Status.LINKED).select_related("patient").first()
    body = {"transactionId": payload.get("transactionId")}
    refs = [cc.get("referenceNumber") for group in payload.get("patient") or [] for cc in group.get("careContexts") or []]
    valid = [r for r in refs if _belongs(hospital, link, r)] if link else []
    if not link or not refs or len(valid) != len(refs):
        body.update(_error("ABDM-1012", "Unknown patient or care context."))
        _reply(callback, "link_on_init", body, hip_id=facility.hip_id)
        return
    expires = timezone.now() + timedelta(minutes=settings.ABDM_LINK_OTP_MINUTES)
    request = LinkRequest.objects.create(
        hospital=hospital, patient=link.patient, abha_link=link, link_reference=uuid.uuid4().hex,
        transaction_id=str(payload.get("transactionId") or ""), references=valid, expires_at=expires,
    )
    issue_otp(hospital, link.patient.mobile, purpose=OTP_PURPOSE)
    body["link"] = {
        "referenceNumber": request.link_reference,
        "authenticationType": "DIRECT",
        "meta": {"communicationMedium": "MOBILE", "communicationHint": "OTP", "communicationExpiry": _iso(expires)},
    }
    _reply(callback, "link_on_init", body, hip_id=facility.hip_id)


def _belongs(hospital, link, ref):
    rk, pk = records.parse_reference(ref)
    if rk is None:
        return False
    return rk.queryset().filter(pk=pk, **{rk.hospital_path: hospital.pk, rk.patient_path: link.patient}).exists()


def handle_link_confirm(callback, payload, facility):
    from apps.patients.registration import verify_otp

    confirmation = payload.get("confirmation") or {}
    request = LinkRequest.objects.filter(hospital=facility.hospital, link_reference=confirmation.get("linkRefNumber"), confirmed_at__isnull=True).select_related("patient", "abha_link").first()
    body = {}
    if request is None or request.expires_at < timezone.now():
        body.update(_error("ABDM-1013", "Link request not found or expired."))
    else:
        ok, reason = verify_otp(facility.hospital, request.patient.mobile, confirmation.get("token", ""), purpose=OTP_PURPOSE)
        if not ok:
            body.update(_error("ABDM-1014", reason))
        else:
            request.confirmed_at = timezone.now()
            request.save(update_fields=["confirmed_at"])
            pairs = _link(request.abha_link, request.references, source=CareContext.Source.PATIENT)
            body["patient"] = _grouped(request.patient, pairs)
    _reply(callback, "link_on_confirm", body, hip_id=facility.hip_id)


def _link(abha_link, references, *, source, status=CareContext.Status.LINKED):
    pairs = []
    for ref in references:
        rk, pk = records.parse_reference(ref)
        record = records.get_record(abha_link.hospital_id, rk.kind, pk) if rk else None
        if record is None:
            continue
        CareContext.objects.update_or_create(
            hospital_id=abha_link.hospital_id, reference=ref,
            defaults={"patient": abha_link.patient, "abha_link": abha_link, "display": records.display(rk, record), "hi_type": rk.hi_type,
                      "source": source, "status": status, "linked_at": timezone.now() if status == CareContext.Status.LINKED else None},
        )
        pairs.append((rk, record))
    return pairs


# --- Hospital-initiated linking (staff link a visit's records) -----------

_LINK_TOKEN_KEY = "abdm:link-token-request:{}"


def link_from_hospital(abha_link, references, facility):
    """Step 1: ask the gateway for a link token for this ABHA; the token
    arrives at handle_link_token, which links the records (step 2)."""
    from apps.patients.models import Patient

    patient = abha_link.patient
    request_id = str(uuid.uuid4())
    _link(abha_link, references, source=CareContext.Source.HOSPITAL, status=CareContext.Status.PENDING)
    cache.set(_LINK_TOKEN_KEY.format(request_id), {"abha_link": abha_link.pk, "references": list(references)}, timeout=3600)
    gender = {Patient.Gender.MALE: "M", Patient.Gender.FEMALE: "F"}.get(patient.gender, "O")
    get_abdm_gateway().send("generate_link_token", {
        "abhaNumber": abha_link.abha_number.replace("-", ""), "abhaAddress": abha_link.abha_address,
        "name": patient.full_name, "gender": gender, "yearOfBirth": patient.birth_year,
    }, hip_id=facility.hip_id, request_id=request_id)


def handle_link_token(callback, payload, facility):
    pending = cache.get(_LINK_TOKEN_KEY.format((payload.get("response") or {}).get("requestId")))
    token = payload.get("linkToken")
    if not pending or not token:
        raise ValueError(f"No link token for this request: {payload.get('error') or 'unknown request'}")
    abha_link = AbhaLink.objects.select_related("patient").get(pk=pending["abha_link"], hospital=facility.hospital)
    pairs = [(rk, records.get_record(facility.hospital_id, rk.kind, pk)) for rk, pk in map(records.parse_reference, pending["references"])]
    get_abdm_gateway().send("link_care_context", {
        "abhaNumber": abha_link.abha_number.replace("-", ""), "abhaAddress": abha_link.abha_address,
        "patient": _grouped(abha_link.patient, [(rk, r) for rk, r in pairs if r is not None]),
    }, hip_id=facility.hip_id, headers={"X-LINK-TOKEN": token})


def handle_link_on_carecontext(callback, payload, facility):
    """The gateway's answer to link_care_context: mark the pending links."""
    if payload.get("error"):
        raise ValueError(f"Linking refused: {payload['error']}")
    refs = [cc.get("referenceNumber") for group in payload.get("patient") or [] for cc in group.get("careContexts") or []]
    qs = CareContext.objects.filter(hospital=facility.hospital, status=CareContext.Status.PENDING)
    if refs:
        qs = qs.filter(reference__in=refs)
    qs.update(status=CareContext.Status.LINKED, linked_at=timezone.now())


# --- Consent --------------------------------------------------------------

def handle_consent_notify(callback, payload, facility):
    notification = payload.get("notification") or {}
    detail = notification.get("consentDetail") or {}
    consent_id = detail.get("consentId") or notification.get("consentId")
    status = str(notification.get("status") or "").lower()
    with transaction.atomic():
        artefact = ConsentArtefact.objects.select_for_update().filter(role=ConsentArtefact.Role.HIP, artefact_id=consent_id).first()
        if artefact is None and detail:
            refs = [c.get("careContextReference") for c in detail.get("careContexts") or []]
            patient_link = AbhaLink.objects.filter(hospital=facility.hospital, abha_address_hash=AbhaLink.address_hash((detail.get("patient") or {}).get("id"))).first()
            permission = detail.get("permission") or {}
            artefact = ConsentArtefact.objects.create(
                hospital=facility.hospital, role=ConsentArtefact.Role.HIP, artefact_id=consent_id,
                patient=patient_link.patient if patient_link else None,
                purpose=(detail.get("purpose") or {}).get("code", ""), hi_types=detail.get("hiTypes") or [],
                care_contexts=[r for r in refs if r], counterparty_id=(detail.get("hiu") or {}).get("id", ""),
                date_from=_dt((permission.get("dateRange") or {}).get("from")), date_to=_dt((permission.get("dateRange") or {}).get("to")),
                expires_at=_dt(permission.get("dataEraseAt")), details=detail,
            )
        if artefact is not None and status in ("revoked", "expired", "denied"):
            artefact.status = status
            artefact.save(update_fields=["status", "updated_at"])
    _reply(callback, "hip_consent_on_notify", {"acknowledgement": {"status": "OK", "consentId": consent_id}}, hip_id=facility.hip_id)


# --- Data flow ------------------------------------------------------------

def handle_hi_request(callback, payload, facility):
    """Acknowledge, then send. The artefact must exist, belong to this
    hospital and still be granted; the requested range must sit inside it."""
    hi = payload.get("hiRequest") or {}
    transaction_id = str(payload.get("transactionId") or "")
    artefact = ConsentArtefact.objects.filter(hospital=facility.hospital, role=ConsentArtefact.Role.HIP, artefact_id=(hi.get("consent") or {}).get("id")).first()
    now = timezone.now()
    rng = hi.get("dateRange") or {}
    date_from, date_to = _dt(rng.get("from")), _dt(rng.get("to"))
    problem = ""
    if artefact is None or not artefact.is_usable(now):
        problem = "Consent artefact not found, revoked or expired."
    elif (artefact.date_from and date_from and date_from < artefact.date_from) or (artefact.date_to and date_to and date_to > artefact.date_to):
        problem = "Requested date range is outside the consent."
    elif not hi.get("dataPushUrl") or not hi.get("keyMaterial"):
        problem = "dataPushUrl and keyMaterial are required."
    status = "ERRORED" if problem else "ACKNOWLEDGED"
    _reply(callback, "hip_hi_on_request", {"hiRequest": {"transactionId": transaction_id, "sessionStatus": status}, **(_error("ABDM-1017", problem) if problem else {})}, hip_id=facility.hip_id)
    if problem:
        return None
    material = hi["keyMaterial"]
    return DataTransfer.objects.create(
        hospital=facility.hospital, role=DataTransfer.Role.HIP, transaction_id=transaction_id, artefact=artefact,
        status=DataTransfer.Status.ACKNOWLEDGED, date_from=date_from, date_to=date_to, data_push_url=hi["dataPushUrl"],
        public_key=(material.get("dhPublicKey") or {}).get("keyValue", ""), nonce=material.get("nonce", ""),
    )


def send_health_information(transfer, facility):
    """Build each consented care context's document, encrypt it for the
    HIU's key, push it, and tell the gateway what was delivered. The HIU's
    key and nonce are what we stored as public_key/nonce."""
    from apps.integrations.abdm_fhir import validate_document_bundle

    artefact = transfer.artefact
    ours = fidelius.generate_key_pair()
    entries, statuses = [], []
    for ref in artefact.care_contexts:
        rk, pk = records.parse_reference(ref)
        record = records.get_record(transfer.hospital_id, rk.kind, pk) if rk else None
        in_range = record is not None and (not transfer.date_from or record.created_at >= transfer.date_from) and (not transfer.date_to or record.created_at <= transfer.date_to)
        if not in_range or (artefact.hi_types and rk.hi_type not in artefact.hi_types):
            statuses.append({"careContextReference": ref, "hiStatus": "ERRORED", "description": "Not available for this consent."})
            continue
        bundle = records.build_bundle(rk, record)
        if validate_document_bundle(bundle):
            statuses.append({"careContextReference": ref, "hiStatus": "ERRORED", "description": "Document failed validation."})
            continue
        data = json.dumps(bundle, separators=(",", ":"), default=str)
        entries.append({
            "content": fidelius.encrypt(data, private_key=ours.private_key, own_nonce=ours.nonce, their_public_key=transfer.public_key, their_nonce=transfer.nonce),
            "media": MEDIA_FHIR, "checksum": fidelius.checksum(data), "careContextReference": ref,
        })
        statuses.append({"careContextReference": ref, "hiStatus": "DELIVERED", "description": "Delivered"})

    gateway = get_abdm_gateway()
    try:
        gateway.push_health_information(url=transfer.data_push_url, payload={
            "pageNumber": 1, "pageCount": 1, "transactionId": transfer.transaction_id,
            "entries": entries, "keyMaterial": fidelius.key_material(ours),
        })
        transfer.status, session = DataTransfer.Status.TRANSFERRED, "TRANSFERRED"
    except Exception as exc:
        logger.error("ABDM data push for transaction %s failed: %s", transfer.transaction_id, exc)
        transfer.status, transfer.error, session = DataTransfer.Status.FAILED, str(exc)[:500], "FAILED"
        statuses = [{**s, "hiStatus": "ERRORED"} if s["hiStatus"] == "DELIVERED" else s for s in statuses]
    transfer.entry_count, transfer.completed_at = len(entries), timezone.now()
    transfer.save(update_fields=["status", "error", "entry_count", "completed_at", "updated_at"])
    gateway.send("hi_notify", {"notification": {
        "consentId": artefact.artefact_id, "transactionId": transfer.transaction_id, "doneAt": _iso(timezone.now()),
        "notifier": {"type": "HIP", "id": facility.hip_id},
        "statusNotification": {"sessionStatus": session, "hipId": facility.hip_id, "statusResponses": statuses},
    }}, hip_id=facility.hip_id)
    return transfer
