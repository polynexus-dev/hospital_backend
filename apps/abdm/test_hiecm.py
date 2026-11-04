"""ABDM HIE-CM: Fidelius encryption, gateway callbacks, M2 (HIP) and M3 (HIU).
The gateway is mocked; the cryptography and our own HIP -> HIU loop are real."""
import base64
import datetime
import json
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.utils import timezone

from apps.patients.models import Patient, Prescription

from . import callbacks, fidelius, hip, hiu
from .gateway import GatewayAuthError, verify_gateway_token
from .models import (
    ABDMFacility, AbhaLink, CareContext, ConsentArtefact, ConsentRequest, DataTransfer, GatewayCallback, LinkRequest,
    ReceivedHealthRecord,
)

ADDRESS = "asha.patil@sbx"


@pytest.fixture
def facility(hospital):
    return ABDMFacility.objects.create(hospital=hospital, hip_id="HIP-1", name="Test Hospital")


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Asha", last_name="Patil", mobile="9822011111",
                                  gender=Patient.Gender.FEMALE, date_of_birth=datetime.date(1990, 5, 1))


@pytest.fixture
def abha(hospital, patient):
    return AbhaLink.objects.create(hospital=hospital, patient=patient, verification_method=AbhaLink.VerificationMethod.MOBILE_OTP,
                                   status=AbhaLink.Status.LINKED, abha_address=ADDRESS, abha_number="91-1234-5678-9012")


@pytest.fixture
def rx(hospital, patient):
    return Prescription.objects.create(hospital=hospital, patient=patient, diagnosis="Hypertension", medications=[{"name": "Amlodipine 5mg", "frequency": "OD"}])


@pytest.fixture
def gateway():
    gw = MagicMock()
    with patch("apps.abdm.hip.get_abdm_gateway", return_value=gw), patch("apps.abdm.hiu.get_abdm_gateway", return_value=gw):
        yield gw


def _callback(request_id="REQ-1", path="x"):
    return GatewayCallback(request_id=request_id, path=path)


def _sent(gw, endpoint):
    calls = [c for c in gw.send.call_args_list if c.args[0] == endpoint]
    assert calls, f"nothing sent to {endpoint}: {[c.args[0] for c in gw.send.call_args_list]}"
    return calls[-1].args[1]


# --- Fidelius ----------------------------------------------------------------

def _affine_mul(k, point):
    P, a = fidelius.P, fidelius.CURVE_A

    def add(p1, p2):
        if p1 is None or p2 is None:
            return p1 or p2
        (x1, y1), (x2, y2) = p1, p2
        if x1 == x2 and (y1 + y2) % P == 0:
            return None
        lam = (3 * x1 * x1 + a) * pow(2 * y1, -1, P) if p1 == p2 else (y2 - y1) * pow(x2 - x1, -1, P)
        x3 = (lam * lam - x1 - x2) % P
        return x3, (lam * (x1 - x3) - y1) % P

    result = None
    while k:
        if k & 1:
            result = add(result, point)
        point, k = add(point, point), k >> 1
    return result


def test_curve_constants_are_the_published_wei25519_ones():
    """Derived from p and A in fidelius.py; these are Bouncy Castle's Curve25519."""
    assert fidelius.CURVE_A == 0x2AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA984914A144
    assert fidelius.CURVE_B == 0x7B425ED097B425ED097B425ED097B425ED097B425ED097B4260B5E9C7710C864
    assert fidelius.GY == 0x20AE19A1B8A086B4E01EDD2C7748D14C923D4D7E6D7C61B229E9C5A27ECED3D9
    assert (fidelius.GY**2 - (fidelius.GX**3 + fidelius.CURVE_A * fidelius.GX + fidelius.CURVE_B)) % fidelius.P == 0


def test_ecdh_matches_plain_weierstrass_arithmetic():
    alice, bob = fidelius.generate_key_pair(), fidelius.generate_key_pair()
    d = int.from_bytes(base64.b64decode(alice.private_key), "little")
    point = fidelius._point_from_spki(base64.b64decode(bob.public_key))
    q_bob = (int.from_bytes(point[1:33], "big"), int.from_bytes(point[33:], "big"))

    assert _affine_mul(d, (fidelius.GX, fidelius.GY))[0] == fidelius._decode_public_key(alice.public_key)
    assert fidelius.shared_secret(alice.private_key, bob.public_key) == _affine_mul(d, q_bob)[0].to_bytes(32, "big")
    assert fidelius.shared_secret(alice.private_key, bob.public_key) == fidelius.shared_secret(bob.private_key, alice.public_key)


def test_public_key_has_abdm_x509_shape():
    key = fidelius.generate_key_pair().public_key
    assert key.startswith("MIIBMTCB6gYHKoZIzj0CATCB3gIBATAr")  # as in ABDM's sample key material
    assert len(base64.b64decode(key)) == 309


def test_encrypt_decrypt_round_trip_and_tamper_detection():
    hip_keys, hiu_keys = fidelius.generate_key_pair(), fidelius.generate_key_pair()
    content = fidelius.encrypt("{\"a\": 1}", private_key=hip_keys.private_key, own_nonce=hip_keys.nonce, their_public_key=hiu_keys.public_key, their_nonce=hiu_keys.nonce)
    opened = dict(private_key=hiu_keys.private_key, own_nonce=hiu_keys.nonce, their_public_key=hip_keys.public_key, their_nonce=hip_keys.nonce)
    assert fidelius.decrypt(content, **opened) == "{\"a\": 1}"

    raw = bytearray(base64.b64decode(content))
    raw[0] ^= 1
    with pytest.raises(fidelius.FideliusError):
        fidelius.decrypt(base64.b64encode(bytes(raw)).decode(), **opened)
    with pytest.raises(fidelius.FideliusError):  # someone else's key
        fidelius.decrypt(content, **{**opened, "private_key": fidelius.generate_key_pair().private_key})


def test_a_point_off_the_curve_is_refused():
    bogus = base64.b64encode(b"\x04" + (5).to_bytes(32, "big") + (7).to_bytes(32, "big")).decode()
    with pytest.raises(fidelius.FideliusError):
        fidelius.shared_secret(fidelius.generate_key_pair().private_key, bogus)


# --- Gateway token -------------------------------------------------------------

def _signed(key, **claims):
    return jwt.encode({"exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5), **claims}, key, algorithm="RS256")


def test_gateway_token_must_be_signed_by_the_gateway_key():
    gateway_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwks = MagicMock()
    jwks.get_signing_key_from_jwt.return_value.key = gateway_key.public_key()
    with patch("apps.abdm.gateway._jwks_client", jwks):
        assert verify_gateway_token("Bearer " + _signed(gateway_key, sub="gateway"))["sub"] == "gateway"
        with pytest.raises(GatewayAuthError):
            verify_gateway_token("Bearer " + _signed(other_key))
        with pytest.raises(GatewayAuthError):
            verify_gateway_token("Bearer " + jwt.encode({"exp": 1}, gateway_key, algorithm="RS256"))  # expired
        with pytest.raises(GatewayAuthError):
            verify_gateway_token("")


# --- Callback endpoint -------------------------------------------------------------

URL = "/api/v1/abdm/callbacks/api/v3/hip/patient/care-context/discover"


@pytest.mark.django_db
def test_callbacks_without_a_valid_gateway_token_are_refused(api_client, facility):
    with patch("apps.abdm.callbacks.verify_gateway_token", side_effect=GatewayAuthError("bad")):
        response = api_client.post(URL, {}, format="json", HTTP_REQUEST_ID="R1", HTTP_X_HIP_ID="HIP-1")
    assert response.status_code == 401
    assert not GatewayCallback.objects.exists()


@pytest.mark.django_db
def test_callback_is_stored_once_and_queued(api_client, facility):
    with patch("apps.abdm.callbacks.verify_gateway_token"), patch("apps.abdm.tasks.process_gateway_callback.delay") as delay, \
            patch("django.db.transaction.on_commit", side_effect=lambda fn: fn()):
        first = api_client.post(URL, {"patient": {"id": ADDRESS}}, format="json", HTTP_REQUEST_ID="R1", HTTP_X_HIP_ID="HIP-1")
        retry = api_client.post(URL, {"patient": {"id": ADDRESS}}, format="json", HTTP_REQUEST_ID="R1", HTTP_X_HIP_ID="HIP-1")
        unknown = api_client.post(URL, {}, format="json", HTTP_REQUEST_ID="R2", HTTP_X_HIP_ID="NOPE")
        no_id = api_client.post(URL, {}, format="json", HTTP_X_HIP_ID="HIP-1")
    assert (first.status_code, retry.status_code, unknown.status_code, no_id.status_code) == (202, 202, 404, 400)
    assert delay.call_count == 1
    stored = GatewayCallback.objects.get()
    assert stored.hospital_id == facility.hospital_id and stored.payload == {"patient": {"id": ADDRESS}}


@pytest.mark.django_db
def test_processing_runs_the_handler_and_drops_the_payload(facility, abha, rx, gateway):
    callback = GatewayCallback.objects.create(request_id="R9", path="hip/patient/care-context/discover", hospital=facility.hospital,
                                              payload={"transactionId": "T", "patient": {"id": ADDRESS}})
    callbacks.process(callback.pk)
    callback.refresh_from_db()
    assert callback.processed_at and callback.payload == {} and callback.error == ""
    assert _sent(gateway, "on_discover")["response"] == {"requestId": "R9"}


# --- HIP: discovery and linking ----------------------------------------------------

@pytest.mark.django_db
def test_discovery_by_abha_address_lists_unlinked_records(facility, abha, rx, gateway):
    hip.handle_discover(_callback(), {"transactionId": "T1", "patient": {"id": ADDRESS}}, facility)
    body = _sent(gateway, "on_discover")
    assert body["matchedBy"] == ["ABHA_ADDRESS"]
    [group] = body["patient"]
    assert group["hiType"] == "Prescription" and group["careContexts"][0]["referenceNumber"] == f"prescription:{rx.pk}"


@pytest.mark.django_db
def test_discovery_by_verified_mobile_and_demographics_links_the_abha(facility, patient, rx, gateway):
    block = {"id": "new.address@sbx", "name": "Asha Patil", "gender": "F", "yearOfBirth": 1990,
             "verifiedIdentifiers": [{"type": "MOBILE", "value": "+91 98220 11111"}]}
    hip.handle_discover(_callback(), {"transactionId": "T1", "patient": block}, facility)
    assert _sent(gateway, "on_discover")["patient"]
    assert AbhaLink.objects.get(patient=patient).abha_address == "new.address@sbx"


@pytest.mark.django_db
def test_discovery_never_guesses_between_two_matches(facility, hospital, patient, gateway):
    Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9822011111", gender=Patient.Gender.FEMALE, date_of_birth=datetime.date(1990, 2, 2))
    block = {"name": "Asha", "gender": "F", "yearOfBirth": 1990, "verifiedIdentifiers": [{"type": "MOBILE", "value": "9822011111"}]}
    hip.handle_discover(_callback(), {"transactionId": "T1", "patient": block}, facility)
    assert _sent(gateway, "on_discover")["error"]["code"] == "ABDM-1010"


@pytest.mark.django_db
def test_patient_initiated_link_needs_the_otp_sent_to_their_mobile(facility, abha, rx, gateway):
    ref = f"prescription:{rx.pk}"
    with patch("apps.patients.registration.secrets.randbelow", return_value=246810):
        hip.handle_link_init(_callback(), {"transactionId": "T2", "abhaAddress": ADDRESS, "patient": [{"careContexts": [{"referenceNumber": ref}]}]}, facility)
    link_ref = _sent(gateway, "link_on_init")["link"]["referenceNumber"]

    hip.handle_link_confirm(_callback("R2"), {"confirmation": {"linkRefNumber": link_ref, "token": "111111"}}, facility)
    assert "error" in _sent(gateway, "link_on_confirm") and not CareContext.objects.exists()

    hip.handle_link_confirm(_callback("R3"), {"confirmation": {"linkRefNumber": link_ref, "token": "246810"}}, facility)
    assert _sent(gateway, "link_on_confirm")["patient"][0]["careContexts"][0]["referenceNumber"] == ref
    assert CareContext.objects.get(reference=ref).status == CareContext.Status.LINKED
    assert LinkRequest.objects.get().confirmed_at


@pytest.mark.django_db
def test_link_init_refuses_another_patients_record(facility, hospital, abha, gateway):
    other = Patient.objects.create(hospital=hospital, first_name="Other", mobile="9000000001")
    theirs = Prescription.objects.create(hospital=hospital, patient=other, diagnosis="x")
    hip.handle_link_init(_callback(), {"abhaAddress": ADDRESS, "patient": [{"careContexts": [{"referenceNumber": f"prescription:{theirs.pk}"}]}]}, facility)
    assert _sent(gateway, "link_on_init")["error"]["code"] == "ABDM-1012"
    assert not LinkRequest.objects.exists()


@pytest.mark.django_db
def test_hospital_initiated_linking(auth_client, facility, abha, rx, gateway):
    ref = f"prescription:{rx.pk}"
    response = auth_client.post(f"/api/v1/abdm/abha-links/{abha.pk}/link_records/", {"references": [ref]}, format="json")
    assert response.status_code == 202
    request_id = gateway.send.call_args.kwargs["request_id"]
    assert CareContext.objects.get(reference=ref).status == CareContext.Status.PENDING

    hip.handle_link_token(_callback(), {"linkToken": "LT-1", "response": {"requestId": request_id}}, facility)
    assert gateway.send.call_args.kwargs["headers"] == {"X-LINK-TOKEN": "LT-1"}
    hip.handle_link_on_carecontext(_callback(), {"patient": [{"careContexts": [{"referenceNumber": ref}]}]}, facility)
    assert CareContext.objects.get(reference=ref).status == CareContext.Status.LINKED

    listing = auth_client.get(f"/api/v1/abdm/abha-links/{abha.pk}/records/").json()
    assert listing == [{"reference": ref, "display": listing[0]["display"], "hi_type": "Prescription", "status": "linked"}]


# --- HIP: consent and data flow -------------------------------------------------------

def _consent_detail(rx, consent_id="C-1"):
    return {"consentId": consent_id, "purpose": {"code": "CAREMGT"}, "patient": {"id": ADDRESS}, "hiu": {"id": "HIU-9"},
            "careContexts": [{"patientReference": "P", "careContextReference": f"prescription:{rx.pk}"}], "hiTypes": ["Prescription"],
            "permission": {"dateRange": {"from": "2000-01-01T00:00:00Z", "to": "2100-01-01T00:00:00Z"}, "dataEraseAt": "2100-01-01T00:00:00Z"}}


@pytest.mark.django_db
def test_consent_notify_stores_and_revokes_the_artefact(facility, abha, rx, gateway):
    hip.handle_consent_notify(_callback(), {"notification": {"status": "GRANTED", "consentDetail": _consent_detail(rx)}}, facility)
    artefact = ConsentArtefact.objects.get(role="hip", artefact_id="C-1")
    assert artefact.patient == abha.patient and artefact.care_contexts == [f"prescription:{rx.pk}"] and artefact.counterparty_id == "HIU-9"
    assert _sent(gateway, "hip_consent_on_notify")["acknowledgement"] == {"status": "OK", "consentId": "C-1"}

    hip.handle_consent_notify(_callback("R2"), {"notification": {"status": "REVOKED", "consentId": "C-1"}}, facility)
    artefact.refresh_from_db()
    assert artefact.status == ConsentArtefact.Status.REVOKED


def _hi_request(consent_id, keys, date_from="2001-01-01T00:00:00Z"):
    return {"transactionId": "TX-1", "hiRequest": {"consent": {"id": consent_id}, "dateRange": {"from": date_from, "to": "2099-01-01T00:00:00Z"},
            "dataPushUrl": "https://hiu.example.in/push", "keyMaterial": fidelius.key_material(keys)}}


@pytest.mark.django_db
def test_health_information_is_encrypted_for_the_requesters_key(facility, abha, rx, gateway):
    hip.handle_consent_notify(_callback(), {"notification": {"status": "GRANTED", "consentDetail": _consent_detail(rx)}}, facility)
    hiu_keys = fidelius.generate_key_pair()

    transfer = hip.handle_hi_request(_callback("R2"), _hi_request("C-1", hiu_keys), facility)
    assert _sent(gateway, "hip_hi_on_request")["hiRequest"]["sessionStatus"] == "ACKNOWLEDGED"
    hip.send_health_information(transfer, facility)

    push = gateway.push_health_information.call_args.kwargs
    assert push["url"] == "https://hiu.example.in/push"
    [entry] = push["payload"]["entries"]
    theirs = push["payload"]["keyMaterial"]
    data = fidelius.decrypt(entry["content"], private_key=hiu_keys.private_key, own_nonce=hiu_keys.nonce,
                            their_public_key=theirs["dhPublicKey"]["keyValue"], their_nonce=theirs["nonce"])
    assert json.loads(data)["resourceType"] == "Bundle" and fidelius.checksum(data) == entry["checksum"]
    assert "Amlodipine" not in json.dumps(push["payload"])  # nothing readable on the wire
    status = _sent(gateway, "hi_notify")["notification"]["statusNotification"]
    assert status["sessionStatus"] == "TRANSFERRED" and status["statusResponses"][0]["hiStatus"] == "DELIVERED"
    transfer.refresh_from_db()
    assert transfer.status == DataTransfer.Status.TRANSFERRED and transfer.entry_count == 1


@pytest.mark.django_db
@pytest.mark.parametrize("problem", ["unknown", "revoked", "outside"])
def test_no_data_without_a_valid_consent(facility, abha, rx, gateway, problem):
    hip.handle_consent_notify(_callback(), {"notification": {"status": "GRANTED", "consentDetail": _consent_detail(rx)}}, facility)
    if problem == "revoked":
        ConsentArtefact.objects.update(status=ConsentArtefact.Status.REVOKED)
    request = _hi_request("NOPE" if problem == "unknown" else "C-1", fidelius.generate_key_pair(), date_from="1990-01-01T00:00:00Z" if problem == "outside" else "2001-01-01T00:00:00Z")

    assert hip.handle_hi_request(_callback("R2"), request, facility) is None
    assert _sent(gateway, "hip_hi_on_request")["hiRequest"]["sessionStatus"] == "ERRORED"
    assert not gateway.push_health_information.called and not DataTransfer.objects.exists()


# --- HIU ---------------------------------------------------------------------------------

@pytest.fixture
def granted(hospital, patient, abha, facility, gateway, settings):
    settings.ABDM_CALLBACK_BASE_URL = "https://apollo.hms.polynexus.in"
    consent = ConsentRequest.objects.create(hospital=hospital, patient=patient, abha_link=abha, date_range_from=datetime.date(2020, 1, 1),
                                            date_range_to=datetime.date(2026, 1, 1), gateway_request_id="OUR-REQ")
    hiu.handle_consent_on_init(_callback(), {"consentRequest": {"id": "CR-1"}, "response": {"requestId": "OUR-REQ"}}, facility)
    hiu.handle_consent_notify(_callback("R2"), {"notification": {"consentRequestId": "CR-1", "status": "GRANTED", "consentArtefacts": [{"id": "A-1"}]}}, facility)
    assert _sent(gateway, "consent_fetch") == {"consentId": "A-1"}
    hiu.handle_consent_on_fetch(_callback("R3"), {"consent": {"status": "GRANTED", "consentDetail": {
        "consentId": "A-1", "hip": {"id": "OTHER-HIP"}, "hiTypes": ["Prescription"], "careContexts": [{"careContextReference": "rx-77"}],
        "permission": {"dateRange": {"from": "2020-01-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"}, "dataEraseAt": "2100-01-01T00:00:00Z"}}}}, facility)
    consent.refresh_from_db()
    return consent


def _push_from_other_hip(transfer, bundle, *, keys=None, page=1, pages=1):
    keys = keys or fidelius.generate_key_pair()
    data = json.dumps(bundle)
    content = fidelius.encrypt(data, private_key=keys.private_key, own_nonce=keys.nonce, their_public_key=transfer.public_key, their_nonce=transfer.nonce)
    return {"pageNumber": page, "pageCount": pages, "transactionId": transfer.transaction_id, "keyMaterial": fidelius.key_material(keys),
            "entries": [{"content": content, "media": "application/fhir+json", "checksum": fidelius.checksum(data), "careContextReference": "rx-77"}]}


BUNDLE = {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "Composition", "title": "Prescription from City Clinic"}}]}


@pytest.mark.django_db
def test_hiu_requests_receives_and_decrypts_records(auth_client, granted, gateway, patient):
    assert granted.status == ConsentRequest.Status.GRANTED and granted.gateway_request_id == "CR-1"
    response = auth_client.post(f"/api/v1/abdm/consent-requests/{granted.pk}/fetch_records/")
    assert response.status_code == 202
    request = _sent(gateway, "hi_request")["hiRequest"]
    assert request["consent"] == {"id": "A-1"} and request["dataPushUrl"] == "https://apollo.hms.polynexus.in/api/v1/abdm/data-push/"
    transfer = DataTransfer.objects.get(role="hiu")
    assert transfer.public_key == request["keyMaterial"]["dhPublicKey"]["keyValue"]
    assert "private_key" not in response.json()[0]

    hiu.handle_hi_on_request(_callback("R4"), {"hiRequest": {"transactionId": "HIU-TX", "sessionStatus": "REQUESTED"},
                                               "response": {"requestId": gateway.send.call_args.kwargs["request_id"]}}, ABDMFacility.objects.get())
    transfer.refresh_from_db()
    assert transfer.transaction_id == "HIU-TX" and transfer.status == DataTransfer.Status.ACKNOWLEDGED

    pushed = auth_client.post("/api/v1/abdm/data-push/", _push_from_other_hip(transfer, BUNDLE), format="json")
    assert pushed.status_code == 202
    record = ReceivedHealthRecord.objects.get()
    assert record.patient == patient and record.bundle == BUNDLE and record.checksum_ok and record.title == "Prescription from City Clinic"
    transfer.refresh_from_db()
    assert transfer.status == DataTransfer.Status.RECEIVED and transfer.private_key == ""  # key gone once done
    assert _sent(gateway, "hi_notify")["notification"]["notifier"] == {"type": "HIU", "id": "HIP-1"}

    # the same transaction is closed now
    assert auth_client.post("/api/v1/abdm/data-push/", _push_from_other_hip(transfer, BUNDLE), format="json").status_code == 400

    listed = auth_client.get(f"/api/v1/abdm/received-records/?patient={patient.pk}").json()
    assert listed["results"][0]["bundle"] == BUNDLE and listed["results"][0]["hip_id"] == "OTHER-HIP"


@pytest.mark.django_db
def test_data_push_that_does_not_decrypt_is_refused(api_client, granted, gateway):
    transfer = hiu.request_records(granted, user=None, facility=ABDMFacility.objects.get())[0]
    DataTransfer.objects.filter(pk=transfer.pk).update(transaction_id="TX-9", status=DataTransfer.Status.ACKNOWLEDGED)
    transfer.refresh_from_db()
    payload = _push_from_other_hip(transfer, BUNDLE)
    payload["entries"][0]["content"] = payload["entries"][0]["content"][:-8] + "AAAAAAA="
    assert api_client.post("/api/v1/abdm/data-push/", payload, format="json").status_code == 400
    assert not ReceivedHealthRecord.objects.exists()


@pytest.mark.django_db
def test_revoked_or_expired_consent_erases_what_it_brought(granted, gateway):
    facility = ABDMFacility.objects.get()
    transfer = hiu.request_records(granted, user=None, facility=facility)[0]
    DataTransfer.objects.filter(pk=transfer.pk).update(transaction_id="TX-5", status=DataTransfer.Status.ACKNOWLEDGED)
    transfer.refresh_from_db()
    hiu.receive_push(_push_from_other_hip(transfer, BUNDLE))
    assert ReceivedHealthRecord.objects.count() == 1

    hiu.handle_consent_notify(_callback("R9"), {"notification": {"consentRequestId": "CR-1", "status": "REVOKED", "consentArtefacts": [{"id": "A-1"}]}}, facility)
    assert not ReceivedHealthRecord.objects.exists()
    assert ConsentArtefact.objects.get(artefact_id="A-1").status == ConsentArtefact.Status.REVOKED


@pytest.mark.django_db
def test_records_are_purged_when_data_erase_at_passes(granted, gateway):
    facility = ABDMFacility.objects.get()
    transfer = hiu.request_records(granted, user=None, facility=facility)[0]
    DataTransfer.objects.filter(pk=transfer.pk).update(transaction_id="TX-6", status=DataTransfer.Status.ACKNOWLEDGED)
    transfer.refresh_from_db()
    hiu.receive_push(_push_from_other_hip(transfer, BUNDLE))

    assert hiu.purge_expired(now=timezone.now()) == 0  # dataEraseAt is 2100
    assert hiu.purge_expired(now=timezone.now() + datetime.timedelta(days=365 * 80)) == 1
    assert not ReceivedHealthRecord.objects.exists()


@pytest.mark.django_db
def test_our_hip_and_our_hiu_interoperate(facility, abha, rx, gateway):
    """Both ends are this code: what the HIP side pushes, the HIU side opens."""
    hip.handle_consent_notify(_callback(), {"notification": {"status": "GRANTED", "consentDetail": _consent_detail(rx)}}, facility)
    consent = ConsentRequest.objects.create(hospital=facility.hospital, patient=abha.patient, abha_link=abha, status="granted",
                                            date_range_from=datetime.date(2001, 1, 1), date_range_to=datetime.date(2099, 1, 1))
    artefact = ConsentArtefact.objects.create(hospital=facility.hospital, role="hiu", artefact_id="C-1", consent_request=consent,
                                              patient=abha.patient, counterparty_id="HIP-1")
    hiu_side = DataTransfer.objects.create(hospital=facility.hospital, role="hiu", artefact=artefact, transaction_id="TX-1",
                                           status=DataTransfer.Status.ACKNOWLEDGED, **{k: v for k, v in vars(fidelius.generate_key_pair()).items()})
    request = {"transactionId": "TX-1", "hiRequest": {"consent": {"id": "C-1"}, "dateRange": {"from": "2001-01-01T00:00:00Z", "to": "2099-01-01T00:00:00Z"},
               "dataPushUrl": "https://x/push", "keyMaterial": {"dhPublicKey": {"keyValue": hiu_side.public_key}, "nonce": hiu_side.nonce}}}

    hip.send_health_information(hip.handle_hi_request(_callback("R2"), request, facility), facility)
    hiu.receive_push(gateway.push_health_information.call_args.kwargs["payload"])

    record = ReceivedHealthRecord.objects.get()
    assert record.checksum_ok and record.care_context_reference == f"prescription:{rx.pk}"
    meds = [e["resource"] for e in record.bundle["entry"] if e["resource"]["resourceType"] == "MedicationRequest"]
    assert meds[0]["medicationCodeableConcept"]["text"] == "Amlodipine 5mg"


@pytest.mark.django_db
def test_received_records_need_clinical_access(restricted_client, granted):
    assert restricted_client.get("/api/v1/abdm/received-records/").status_code == 403
