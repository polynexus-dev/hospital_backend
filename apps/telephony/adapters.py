"""
Pluggable telephony provider adapters. The hospital's cloud telephony / IVR
vendor is not selected yet, so every provider talks through this interface:
inbound events arrive as a webhook payload the adapter normalizes into the
shape `views.TelephonyWebhookView` expects, and outbound calls are placed
through `initiate_call`. Swapping vendors later means adding one adapter
class and pointing settings.TELEPHONY_PROVIDER at it — no other code changes.
"""
from abc import ABC, abstractmethod

from django.conf import settings
from django.utils.dateparse import parse_datetime


class TelephonyProvider(ABC):
    @abstractmethod
    def normalize_webhook_payload(self, payload: dict) -> dict:
        """Map a provider-specific webhook body to the fields
        apps.telephony.models.Call needs: direction, status, from_number,
        to_number, started_at, answered_at, ended_at, duration_seconds,
        recording_url, provider_call_id."""

    @abstractmethod
    def initiate_call(self, *, from_number: str, to_number: str) -> str:
        """Places an outbound / click-to-call call. Returns the provider's
        call id."""


class StubTelephonyProvider(TelephonyProvider):
    """No real vendor wired up yet. Normalizes a generic JSON shape so the
    rest of the system (callback queue, screen-pop, MIS) can be built and
    tested end-to-end before a vendor contract is signed."""

    def normalize_webhook_payload(self, payload: dict) -> dict:
        return {
            "direction": payload.get("direction", "inbound"),
            "status": payload.get("status", "missed"),
            "from_number": payload.get("from_number", ""),
            "to_number": payload.get("to_number", ""),
            "started_at": parse_datetime(payload["started_at"]) if payload.get("started_at") else None,
            "answered_at": parse_datetime(payload["answered_at"]) if payload.get("answered_at") else None,
            "ended_at": parse_datetime(payload["ended_at"]) if payload.get("ended_at") else None,
            "duration_seconds": payload.get("duration_seconds", 0),
            "recording_url": payload.get("recording_url", ""),
            "provider_call_id": payload.get("call_id", ""),
        }

    def initiate_call(self, *, from_number: str, to_number: str) -> str:
        return f"stub-call-{from_number}-{to_number}"


def get_telephony_provider() -> TelephonyProvider:
    providers = {"stub": StubTelephonyProvider}
    provider_cls = providers.get(settings.TELEPHONY_PROVIDER, StubTelephonyProvider)
    return provider_cls()
