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
from abc import ABC, abstractmethod
from datetime import date

from django.conf import settings


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


def get_abdm_gateway() -> ABDMGateway:
    gateways = {"stub": StubABDMGateway}
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
