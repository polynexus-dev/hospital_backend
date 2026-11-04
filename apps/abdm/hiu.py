"""
This hospital as Health Information User (ABDM milestone M3): seeing a
patient's records from other providers, with their consent.

  consent request   staff ask (ConsentRequestViewSet); the gateway answers
                    with the request id (on-init), later with the patient's
                    decision and artefact ids (notify)
  artefacts         we fetch each one: which HIP, which care contexts, what
                    range, until when (on-fetch)
  data request      per artefact, a fresh ECDH key pair; the gateway confirms
                    (on-request) and the HIP pushes encrypted bundles to our
                    dataPushUrl
  receipt           decrypt, check the checksum, store encrypted at rest,
                    tell the gateway what arrived
  erasure           a revoked or expired consent deletes what it brought in —
                    an ABDM obligation, not a choice
"""
import json
import logging
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import fidelius
from .gateway import GatewayNotConfigured, get_abdm_gateway
from .hip import _dt, _iso
from .models import ConsentArtefact, ConsentRequest, DataTransfer, ReceivedHealthRecord

logger = logging.getLogger(__name__)

DATA_PUSH_PATH = "/api/v1/abdm/data-push/"


def _hiu_id(facility):
    return facility.hiu_id or facility.hip_id


def _reply(callback, endpoint, body, facility):
    get_abdm_gateway().send(endpoint, {**body, "response": {"requestId": callback.request_id}}, hiu_id=_hiu_id(facility))


# --- Consent lifecycle -----------------------------------------------------

def handle_consent_on_init(callback, payload, facility):
    """Our consent request reached the Consent Manager: record its id."""
    request_id = (payload.get("response") or {}).get("requestId")
    consent_request_id = (payload.get("consentRequest") or {}).get("id")
    qs = ConsentRequest.objects.filter(hospital=facility.hospital, gateway_request_id=request_id)
    if payload.get("error"):
        qs.update(status=ConsentRequest.Status.DENIED, responded_at=timezone.now())
    elif consent_request_id:
        qs.update(gateway_request_id=consent_request_id)


def handle_consent_notify(callback, payload, facility):
    notification = payload.get("notification") or {}
    status = str(notification.get("status") or "").lower()
    consent_request = ConsentRequest.objects.filter(hospital=facility.hospital, gateway_request_id=notification.get("consentRequestId")).first()
    artefact_ids = [a.get("id") for a in notification.get("consentArtefacts") or [] if a.get("id")]
    if consent_request is not None and status in ConsentRequest.Status.values:
        consent_request.status = status
        consent_request.responded_at = consent_request.responded_at or timezone.now()
        if artefact_ids:
            consent_request.consent_artifact_id = artefact_ids[0]
        consent_request.save(update_fields=["status", "responded_at", "consent_artifact_id"])
    if status == ConsentRequest.Status.GRANTED and consent_request is not None:
        for artefact_id in artefact_ids:
            ConsentArtefact.objects.get_or_create(
                role=ConsentArtefact.Role.HIU, artefact_id=artefact_id,
                defaults={"hospital": facility.hospital, "consent_request": consent_request, "patient": consent_request.patient, "purpose": consent_request.purpose},
            )
            get_abdm_gateway().send("consent_fetch", {"consentId": artefact_id}, hiu_id=_hiu_id(facility))
    elif status in ("revoked", "expired", "denied"):
        ids = artefact_ids or list(ConsentArtefact.objects.filter(role=ConsentArtefact.Role.HIU, consent_request=consent_request).values_list("artefact_id", flat=True))
        erase(ConsentArtefact.objects.filter(hospital=facility.hospital, role=ConsentArtefact.Role.HIU, artefact_id__in=ids), status)
    _reply(callback, "hiu_consent_on_notify", {"acknowledgement": [{"status": "OK", "consentId": a} for a in artefact_ids]}, facility)


def handle_consent_on_fetch(callback, payload, facility):
    consent = payload.get("consent") or {}
    detail = consent.get("consentDetail") or {}
    artefact = ConsentArtefact.objects.filter(hospital=facility.hospital, role=ConsentArtefact.Role.HIU, artefact_id=detail.get("consentId")).first()
    if artefact is None:
        raise ValueError("Artefact we did not ask for.")
    permission = detail.get("permission") or {}
    rng = permission.get("dateRange") or {}
    artefact.status = str(consent.get("status") or "granted").lower()
    artefact.hi_types = detail.get("hiTypes") or []
    artefact.care_contexts = [c.get("careContextReference") for c in detail.get("careContexts") or [] if c.get("careContextReference")]
    artefact.counterparty_id = (detail.get("hip") or {}).get("id", "")
    artefact.date_from, artefact.date_to = _dt(rng.get("from")), _dt(rng.get("to"))
    artefact.expires_at = _dt(permission.get("dataEraseAt"))
    artefact.details = detail
    artefact.save()


# --- Requesting and receiving data --------------------------------------------

def request_records(consent_request, *, user, facility):
    """One data request per usable artefact of this consent. Returns the
    transfers started; the records arrive later at the data-push URL."""
    if not settings.ABDM_CALLBACK_BASE_URL:
        raise GatewayNotConfigured("Set ABDM_CALLBACK_BASE_URL (this site's public HTTPS address) so providers can send records here.")
    now = timezone.now()
    started = []
    for artefact in consent_request.artefacts.filter(role=ConsentArtefact.Role.HIU):
        if not artefact.is_usable(now) or not artefact.counterparty_id:
            continue  # not fetched yet, or no longer valid
        keys = fidelius.generate_key_pair()
        request_id = str(uuid.uuid4())
        transfer = DataTransfer.objects.create(
            hospital=consent_request.hospital, role=DataTransfer.Role.HIU, artefact=artefact, gateway_request_id=request_id,
            date_from=artefact.date_from, date_to=artefact.date_to, private_key=keys.private_key, public_key=keys.public_key,
            nonce=keys.nonce, data_push_url=settings.ABDM_CALLBACK_BASE_URL.rstrip("/") + DATA_PUSH_PATH, requested_by=user,
        )
        get_abdm_gateway().send("hi_request", {"hiRequest": {
            "consent": {"id": artefact.artefact_id},
            "dateRange": {"from": _iso(artefact.date_from), "to": _iso(artefact.date_to)},
            "dataPushUrl": transfer.data_push_url,
            "keyMaterial": fidelius.key_material(keys),
        }}, hiu_id=_hiu_id(facility), request_id=request_id)
        started.append(transfer)
    return started


def handle_hi_on_request(callback, payload, facility):
    hi = payload.get("hiRequest") or {}
    transfer = DataTransfer.objects.filter(hospital=facility.hospital, role=DataTransfer.Role.HIU, gateway_request_id=(payload.get("response") or {}).get("requestId")).first()
    if transfer is None:
        raise ValueError("Data request we did not make.")
    transfer.transaction_id = str(hi.get("transactionId") or "")
    if payload.get("error") or str(hi.get("sessionStatus", "")).upper() == "ERRORED":
        transfer.status, transfer.error, transfer.private_key = DataTransfer.Status.FAILED, str(payload.get("error") or "Refused by the provider.")[:500], ""
    else:
        transfer.status = DataTransfer.Status.ACKNOWLEDGED
    transfer.save(update_fields=["transaction_id", "status", "error", "private_key", "updated_at"])


class DataPushRejected(Exception):
    pass


def receive_push(payload):
    """The HIP's data push. Only a transfer we started and that is still
    open accepts it; content that doesn't decrypt with that transfer's key
    is refused. Returns the transfer."""
    transaction_id = str(payload.get("transactionId") or "")
    with transaction.atomic():
        transfer = (DataTransfer.objects.select_for_update().select_related("artefact", "artefact__consent_request")
                    .filter(role=DataTransfer.Role.HIU, transaction_id=transaction_id, status=DataTransfer.Status.ACKNOWLEDGED).exclude(transaction_id="").first())
        if transfer is None or not transfer.private_key:
            raise DataPushRejected("Unknown or closed transaction.")
        material = payload.get("keyMaterial") or {}
        their_key, their_nonce = (material.get("dhPublicKey") or {}).get("keyValue", ""), material.get("nonce", "")
        statuses = []
        for entry in payload.get("entries") or []:
            ref = entry.get("careContextReference", "")
            if "content" not in entry:
                statuses.append({"careContextReference": ref, "hiStatus": "ERRORED", "description": "Linked (non-inline) entries aren't supported."})
                continue
            try:
                data = fidelius.decrypt(entry["content"], private_key=transfer.private_key, own_nonce=transfer.nonce, their_public_key=their_key, their_nonce=their_nonce)
                bundle = json.loads(data)
            except (fidelius.FideliusError, ValueError) as exc:
                raise DataPushRejected(f"Entry for {ref or 'a care context'} could not be decrypted.") from exc
            ReceivedHealthRecord.objects.create(
                hospital_id=transfer.hospital_id, transfer=transfer, patient=transfer.artefact.patient, care_context_reference=ref[:100],
                title=_title(bundle), bundle=bundle, checksum_ok=fidelius.checksum(data) == entry.get("checksum"),
            )
            statuses.append({"careContextReference": ref, "hiStatus": "OK", "description": "Received"})
        transfer.entry_count += len([s for s in statuses if s["hiStatus"] == "OK"])
        last_page = int(payload.get("pageNumber") or 1) >= int(payload.get("pageCount") or 1)
        if last_page:
            transfer.status, transfer.completed_at, transfer.private_key = DataTransfer.Status.RECEIVED, timezone.now(), ""
        transfer.save(update_fields=["entry_count", "status", "completed_at", "private_key", "updated_at"])
    _notify_received(transfer, statuses)
    return transfer


def _title(bundle):
    for entry in bundle.get("entry") or []:
        resource = entry.get("resource") or {}
        if resource.get("resourceType") == "Composition":
            return str(resource.get("title") or "")[:200]
    return ""


def _notify_received(transfer, statuses):
    from .models import ABDMFacility

    facility = ABDMFacility.objects.filter(hospital_id=transfer.hospital_id).first()
    if facility is None:
        return
    try:
        get_abdm_gateway().send("hi_notify", {"notification": {
            "consentId": transfer.artefact.artefact_id, "transactionId": transfer.transaction_id, "doneAt": _iso(timezone.now()),
            "notifier": {"type": "HIU", "id": _hiu_id(facility)},
            "statusNotification": {"sessionStatus": "TRANSFERRED", "hipId": transfer.artefact.counterparty_id, "statusResponses": statuses},
        }}, hiu_id=_hiu_id(facility))
    except Exception as exc:  # the records are stored; a failed status note mustn't undo that
        logger.error("ABDM receipt notification for transaction %s failed: %s", transfer.transaction_id, exc)


# --- Erasure ---------------------------------------------------------------

def erase(artefacts, status):
    """Delete what these artefacts brought in and mark them `status`."""
    ids = list(artefacts.values_list("pk", flat=True))
    deleted, _ = ReceivedHealthRecord.objects.filter(transfer__artefact_id__in=ids).delete()
    DataTransfer.objects.filter(artefact_id__in=ids).update(private_key="")
    ConsentArtefact.objects.filter(pk__in=ids).update(status=status, updated_at=timezone.now())
    return deleted


def purge_expired(now=None):
    """Daily: consents whose dataEraseAt has passed lose their records."""
    now = now or timezone.now()
    expired = ConsentArtefact.objects.filter(role=ConsentArtefact.Role.HIU, expires_at__lte=now).exclude(status=ConsentArtefact.Status.EXPIRED)
    return erase(expired, ConsentArtefact.Status.EXPIRED)
