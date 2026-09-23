"""
Pluggable gateways for ABDM (ABHA linking + HIE-CM consent/health-record
exchange) and NHCX (claims exchange) — same shape as apps.integrations.
connectors.HISConnector: an abstract interface plus a selector function
reading a settings flag, so a real implementation is a drop-in class, not
a rewrite of anything that calls it.

Deliberately NOT following apps.integrations.connectors.StubHISConnector's
"return empty data" convention for the stub here. An empty HIS sync is a
legitimate steady state (no visits yet); a *fake successful* ABHA link or
consent grant is never a legitimate steady state — it's the difference
between "no data yet" and "this claims something happened that didn't."
So both stubs below raise clearly instead, and callers (see views.py)
turn that into a clean, honest 503 rather than a fabricated success.
"""
import logging
from abc import ABC, abstractmethod
from datetime import date

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


class GatewayNotConfigured(Exception):
    """Raised by the stub gateways below — no ABDM_GATEWAY/NHCX_GATEWAY
    other than "stub" has been implemented and selected yet. Views catch
    this and return HTTP 503, not a fabricated success response."""


# --- ABDM: ABHA linking + HIE-CM consent -----------------------------------


class ABDMGateway(ABC):
    @abstractmethod
    def initiate_abha_verification(self, *, identifier: str, method: str) -> dict:
        """Starts OTP verification for `identifier` (a mobile number or
        Aadhaar number, per `method`). Returns {"txn_id": str}."""

    @abstractmethod
    def verify_abha_otp(self, *, txn_id: str, otp: str) -> dict:
        """Returns {"abha_number": str, "abha_address": str} once the OTP
        confirms the identity ABDM already holds for this ABHA."""

    @abstractmethod
    def create_consent_request(
        self, *, abha_address: str, purpose: str, hi_types: list[str], date_from: date, date_to: date,
    ) -> dict:
        """Relays a consent request to the patient's Consent Manager.
        Returns {"gateway_request_id": str}."""

    @abstractmethod
    def fetch_consent_status(self, *, gateway_request_id: str) -> dict:
        """Returns {"status": "granted"|"denied"|"expired"|"requested",
        "consent_artifact_id": str | None, "expires_at": datetime | None}."""

    @abstractmethod
    def fetch_health_records(self, *, consent_artifact_id: str) -> dict:
        """Returns the HI (Health Information) bundle authorized by the
        given consent artifact. Shape is FHIR-bundle-like; deliberately
        untyped here since it's entirely gateway-defined until one is
        actually implemented against ABDM's real response format."""


class StubABDMGateway(ABDMGateway):
    def initiate_abha_verification(self, *, identifier: str, method: str) -> dict:
        raise GatewayNotConfigured("ABDM is not connected yet — set ABDM_GATEWAY to a real implementation.")

    def verify_abha_otp(self, *, txn_id: str, otp: str) -> dict:
        raise GatewayNotConfigured("ABDM is not connected yet — set ABDM_GATEWAY to a real implementation.")

    def create_consent_request(self, *, abha_address, purpose, hi_types, date_from, date_to) -> dict:
        raise GatewayNotConfigured("ABDM is not connected yet — set ABDM_GATEWAY to a real implementation.")

    def fetch_consent_status(self, *, gateway_request_id: str) -> dict:
        raise GatewayNotConfigured("ABDM is not connected yet — set ABDM_GATEWAY to a real implementation.")

    def fetch_health_records(self, *, consent_artifact_id: str) -> dict:
        raise GatewayNotConfigured("ABDM is not connected yet — set ABDM_GATEWAY to a real implementation.")


class ABDMGatewayError(Exception):
    """Raised when the real gateway got a response from ABDM but it wasn't
    a success — a distinct exception from GatewayNotConfigured (that one
    means "nothing is wired up at all"; this one means "it's wired up and
    ABDM said no/errored"). Callers should treat this the same way they'd
    treat any other downstream failure, not the same way as "not connected"."""


# ABDM Gateway API v3 (the current version as of this integration's initial
# build, 2026-09-22 — see the module note in RealABDMGateway below on why
# that date matters). Session/auth path confirmed against ABDM's own sandbox
# Postman collection; ABHA-verification and consent payload/response field
# names are this integration's best-effort reading of the same source and
# have NOT been exercised against a live sandbox response, since this
# hospital's ABDM sandbox credentials were only applied for today and
# haven't arrived yet. See the module docstring in RealABDMGateway.
_ABDM_TOKEN_CACHE_KEY = "abdm_gateway:access_token"

# apps.abdm.models.AbhaLink.VerificationMethod -> ABDM's `loginHint` enum
# for /v3/profile/login/request/otp. Best-effort mapping — confirm the
# right-hand values against the sandbox docs; "existing_abha" in particular
# may need to be "abha-number" or "abha-address" depending on exactly which
# identifier the caller supplied.
_LOGIN_HINT_BY_METHOD = {
    "aadhaar_otp": "aadhaar",
    "mobile_otp": "mobile",
    "existing_abha": "abha-number",
}


class RealABDMGateway(ABDMGateway):
    """HTTP client for ABDM's Gateway v3 sandbox/production API.

    STATUS AS OF THIS WRITING (2026-09-22): this hospital's ABDM sandbox
    application was submitted today — no client_id/client_secret exist
    yet, so nothing below has been run against a live ABDM response. What
    IS grounded: the session-token endpoint path and payload shape, taken
    from ABDM's own published sandbox Postman collection
    (github.com/Nirmitee-tech/abdm-v3-postman-collection) — that part
    should work as written. The ABHA-verification and consent endpoints'
    *paths* come from the same source; their exact request/response FIELD
    NAMES are this integration's best-effort reconstruction from general
    ABDM API conventions, not a field-by-field read of a live response,
    because no live response was available to read. Treat every
    `response.json()[...]` access below as the first thing to check
    against a real sandbox call once credentials arrive — if a field name
    is wrong you'll get a clean KeyError inside `_request`'s try/except,
    surfaced as an ABDMGatewayError with ABDM's raw response body attached,
    not a silent wrong result.

    Before this can go live, in order:
    1. Complete NHA sandbox onboarding: register this facility, get
       ABDM_CLIENT_ID/ABDM_CLIENT_SECRET, and register this hospital's
       ABDM_HIP_ID against the Health Facility Registry.
    2. Register HIP/HIU callback URLs in the NHA sandbox portal for the
       endpoints this app doesn't have yet — the consent flow ABDM
       actually uses is push (ABDM calls YOUR `on-init`/`on-status`
       endpoints), not pure polling. `create_consent_request` and
       `fetch_consent_status` below call the synchronous
       request/status-check endpoints the Postman collection also lists,
       which cover this app's current poll-based ConsentRequestViewSet.
       check_status action without needing callback endpoints yet — but a
       production HIU integration should eventually add them rather than
       rely on polling alone.
    3. Set ABDM_GATEWAY=real, ABDM_BASE_URL (sandbox host), ABDM_CLIENT_ID,
       ABDM_CLIENT_SECRET, ABDM_HIP_ID in the environment.
    4. Run a real end-to-end test against the sandbox — start with
       initiate_abha_verification against a sandbox test identifier (NHA's
       sandbox provides these), fix any field-name mismatches this
       surfaces, then move on to consent.
    5. fetch_health_records is intentionally NOT implemented as a real
       call yet (still raises GatewayNotConfigured) — ABDM's actual data
       flow is asynchronous (HIU requests via
       /data-flow/v3/health-information/request, then the HIP pushes an
       ECDH-encrypted FHIR bundle to a callback endpoint this app doesn't
       expose yet) rather than "call an endpoint, get the bundle back"
       the way ConsentRequestViewSet.fetch_records currently assumes.
       Wiring this for real needs: a data-push callback endpoint, and
       implementing ABDM's key-exchange/encryption spec for that payload
       — deliberately scoped out of this pass rather than half-built.
    """

    def _get_access_token(self) -> str:
        cached = cache.get(_ABDM_TOKEN_CACHE_KEY)
        if cached:
            return cached

        try:
            response = requests.post(
                f"{settings.ABDM_BASE_URL}/gateway/v3/sessions",
                json={
                    "clientId": settings.ABDM_CLIENT_ID,
                    "clientSecret": settings.ABDM_CLIENT_SECRET,
                    "grantType": "client_credentials",
                },
                timeout=settings.ABDM_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            token = data["accessToken"]
        except (requests.RequestException, KeyError, ValueError) as exc:
            logger.error("ABDM session-token request failed: %s", exc)
            raise ABDMGatewayError(f"Could not obtain an ABDM session token: {exc}") from exc

        # expiresIn is seconds (ABDM sandbox default is short-lived, ~30min)
        # — cache for somewhat less so a token never gets handed to a
        # caller moments before it expires mid-request.
        expires_in = int(data.get("expiresIn", 300))
        cache.set(_ABDM_TOKEN_CACHE_KEY, token, timeout=max(expires_in - 30, 30))
        return token

    def _post(self, path: str, *, payload: dict) -> dict:
        """Shared POST-with-bearer-token plumbing for every ABDM call
        below. Raises ABDMGatewayError on any failure — network, non-2xx,
        or an unexpected response shape — with ABDM's raw response body
        logged (never patient-identifying request payload contents, per
        this codebase's logging convention — see config/settings/base.py's
        LOGGING comment) so a field-name mismatch is diagnosable."""
        headers = {
            "Authorization": f"Bearer {self._get_access_token()}",
            "X-CM-ID": "sbx",  # ABDM sandbox's fixed Consent Manager id; not applicable in production
            "REQUEST-ID": _new_request_id(),
            "TIMESTAMP": _iso_timestamp(),
            "Content-Type": "application/json",
        }
        try:
            response = requests.post(f"{settings.ABDM_BASE_URL}{path}", json=payload, headers=headers, timeout=settings.ABDM_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.json() if response.content else {}
        except requests.RequestException as exc:
            body = getattr(exc.response, "text", "")
            logger.error("ABDM request to %s failed: %s — response body: %s", path, exc, body)
            raise ABDMGatewayError(f"ABDM request to {path} failed: {exc}") from exc

    def initiate_abha_verification(self, *, identifier: str, method: str) -> dict:
        login_hint = _LOGIN_HINT_BY_METHOD.get(method, method)
        try:
            data = self._post(
                "/v3/profile/login/request/otp",
                payload={"scope": ["abha-login"], "loginHint": login_hint, "loginId": identifier, "otpSystem": "abdm"},
            )
            return {"txn_id": data["txnId"]}
        except KeyError as exc:
            raise ABDMGatewayError(f"Unexpected response shape from ABDM OTP-request endpoint: missing {exc}") from exc

    def verify_abha_otp(self, *, txn_id: str, otp: str) -> dict:
        try:
            data = self._post(
                "/v3/profile/login/verify",
                payload={"scope": ["abha-login"], "authData": {"authMethods": ["otp"], "otp": {"txnId": txn_id, "otpValue": otp}}},
            )
            account = data["accounts"][0] if data.get("accounts") else data
            return {"abha_number": account["ABHANumber"], "abha_address": account["preferredAbhaAddress"]}
        except (KeyError, IndexError) as exc:
            raise ABDMGatewayError(f"Unexpected response shape from ABDM OTP-verify endpoint: missing {exc}") from exc

    def create_consent_request(self, *, abha_address: str, purpose: str, hi_types: list[str], date_from: date, date_to: date) -> dict:
        try:
            data = self._post(
                "/consent/v3/request/init",
                payload={
                    "consent": {
                        "purpose": {"code": purpose},
                        "patient": {"id": abha_address},
                        "hiTypes": hi_types,
                        "permission": {
                            "dateRange": {"from": date_from.isoformat(), "to": date_to.isoformat()},
                            "dataEraseAt": date_to.isoformat(),
                            "frequency": {"unit": "HOUR", "value": 1, "repeats": 0},
                        },
                        "hip": {"id": settings.ABDM_HIP_ID} if settings.ABDM_HIP_ID else None,
                    },
                },
            )
            return {"gateway_request_id": data["consentRequestId"]}
        except KeyError as exc:
            raise ABDMGatewayError(f"Unexpected response shape from ABDM consent-init endpoint: missing {exc}") from exc

    def fetch_consent_status(self, *, gateway_request_id: str) -> dict:
        try:
            data = self._post("/consent/v3/request/status", payload={"consentRequestId": gateway_request_id})
            # ABDM's status values are its own vocabulary (e.g. "GRANTED",
            # "DENIED", "EXPIRED", "REQUESTED") — lower-cased here to match
            # apps.abdm.models.ConsentRequest.Status, which views.py writes
            # straight through without its own translation step.
            return {
                "status": str(data["status"]).lower(),
                "consent_artifact_id": data.get("consentArtefacts", [{}])[0].get("id") if data.get("consentArtefacts") else None,
                "expires_at": data.get("consentArtefacts", [{}])[0].get("expiryTime") if data.get("consentArtefacts") else None,
            }
        except KeyError as exc:
            raise ABDMGatewayError(f"Unexpected response shape from ABDM consent-status endpoint: missing {exc}") from exc

    def fetch_health_records(self, *, consent_artifact_id: str) -> dict:
        # Deliberately still not implemented as a real call — see this
        # class's docstring, point 5.
        raise GatewayNotConfigured(
            "ABDM health-record fetch is not wired up yet — it needs an async data-push callback endpoint "
            "and ABDM's key-exchange/encryption scheme, not just an HTTP call. See RealABDMGateway's docstring."
        )


def _new_request_id() -> str:
    import uuid

    return str(uuid.uuid4())


def _iso_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def get_abdm_gateway() -> ABDMGateway:
    gateways = {"stub": StubABDMGateway, "real": RealABDMGateway}
    gateway_cls = gateways.get(settings.ABDM_GATEWAY, StubABDMGateway)
    return gateway_cls()


# --- NHCX: claims exchange ---------------------------------------------------


class NHCXGateway(ABC):
    @abstractmethod
    def submit_transaction(self, *, transaction_type: str, payload: dict) -> dict:
        """Submits an eligibility-check/pre-auth/claim transaction.
        Returns {"nhcx_transaction_id": str, "status": str}."""

    @abstractmethod
    def fetch_transaction_status(self, *, nhcx_transaction_id: str) -> dict:
        """Returns {"status": str, "summary": str}."""


class StubNHCXGateway(NHCXGateway):
    def submit_transaction(self, *, transaction_type: str, payload: dict) -> dict:
        raise GatewayNotConfigured("NHCX is not connected yet — set NHCX_GATEWAY to a real implementation.")

    def fetch_transaction_status(self, *, nhcx_transaction_id: str) -> dict:
        raise GatewayNotConfigured("NHCX is not connected yet — set NHCX_GATEWAY to a real implementation.")


def get_nhcx_gateway() -> NHCXGateway:
    gateways = {"stub": StubNHCXGateway}
    gateway_cls = gateways.get(settings.NHCX_GATEWAY, StubNHCXGateway)
    return gateway_cls()
