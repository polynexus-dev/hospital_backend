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
            "from_number": str(payload.get("caller") or payload.get("from_number") or ""),
            "to_number": str(payload.get("callee") or payload.get("to_number") or ""),
            "started_at": parse_datetime(payload.get("start_date") or payload.get("started_at")) if (payload.get("start_date") or payload.get("started_at")) else None,
            "answered_at": parse_datetime(payload.get("answer_date") or payload.get("answered_at")) if (payload.get("answer_date") or payload.get("answered_at")) else None,
            "ended_at": parse_datetime(payload.get("end_date") or payload.get("ended_at")) if (payload.get("end_date") or payload.get("ended_at")) else None,
            "duration_seconds": int(float(payload.get("duration") or payload.get("duration_seconds") or 0)),
            "recording_url": payload.get("recording_url", ""),
            "provider_call_id": str(payload.get("callid") or payload.get("call_id") or ""),
        }

    def initiate_call(self, *, from_number: str, to_number: str) -> str:
        return f"stub-call-{from_number}-{to_number}"


class HoduPBXProvider(TelephonyProvider):
    """
    Telephony provider adapter for HoduPBX Tenant API (v1.4).
    Handles HoduPBX call event normalization, click-to-call, active calls,
    and recording audio retrieval.
    """

    def __init__(self):
        self.base_url = getattr(settings, "HODUPBX_BASE_URL", "").rstrip("/")
        self.token_id = getattr(settings, "HODUPBX_TOKEN_ID", "")
        self.tenant_id = getattr(settings, "HODUPBX_TENANT_ID", "1000")
        # MD5 of the extension's web password (from HoduPBX extension settings)
        self.extension_password_md5 = getattr(settings, "HODUPBX_EXTENSION_PASSWORD_MD5", "")

    def _parse_dt(self, val):
        if not val:
            return None
        dt = parse_datetime(val)
        if dt is None:
            try:
                from datetime import datetime
                dt = datetime.strptime(str(val), "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None
        from django.utils import timezone
        if timezone.is_naive(dt):
            return timezone.make_aware(dt)
        return dt

    def normalize_webhook_payload(self, payload: dict) -> dict:
        """
        Maps HoduPBX webhook / CDR payload to Call model schema.
        Handles both HoduPBX parameter naming (caller/callee/start_date/callid)
        and standard generic webhooks.
        """
        raw_status = str(payload.get("status", "")).lower()
        duration = int(float(payload.get("duration") or payload.get("duration_seconds") or 0))

        if raw_status in ("answered", "connected", "completed", "success"):
            status = "answered"
        elif raw_status in ("missed", "no_answer", "noanswer"):
            status = "missed"
        elif raw_status in ("busy",):
            status = "busy"
        elif raw_status in ("failed",):
            status = "failed"
        elif raw_status in ("voicemail",):
            status = "voicemail"
        else:
            status = "answered" if duration > 0 else "missed"

        direction = payload.get("direction", "inbound")
        if direction not in ("inbound", "outbound"):
            direction = "inbound"

        caller = str(payload.get("caller") or payload.get("from_number") or "")
        callee = str(payload.get("callee") or payload.get("to_number") or "")
        call_id = str(payload.get("callid") or payload.get("call_id") or "")

        start_dt = self._parse_dt(payload.get("start_date") or payload.get("started_at"))
        answer_dt = self._parse_dt(payload.get("answer_date") or payload.get("answered_at"))
        end_dt = self._parse_dt(payload.get("end_date") or payload.get("ended_at"))

        recording_url = payload.get("recording_url", "")
        # If call finished with duration and call_id, resolve recording if not provided
        if not recording_url and call_id and duration > 0 and self.token_id:
            try:
                recording_url = self.get_recording_url(call_id)
            except Exception:
                pass

        return {
            "direction": direction,
            "status": status,
            "from_number": caller,
            "to_number": callee,
            "started_at": start_dt,
            "answered_at": answer_dt,
            "ended_at": end_dt,
            "duration_seconds": duration,
            "recording_url": recording_url,
            "provider_call_id": call_id,
        }

    def initiate_call(self, *, from_number: str, to_number: str) -> str:
        """
        HoduPBX Click2Call API — /api/info/click2call
        First rings the agent extension; once answered, bridges the call to phone_number.
        extension_password must be the MD5 hash of the extension's web password.
        """
        import os
        import requests

        if not self.base_url or not self.token_id:
            print(f"[HoduPBX] ERROR: base_url={self.base_url!r} token_id={self.token_id!r} — not configured")
            return f"hodu-unconfigured-{from_number}-{to_number}"

        ext_to_use = (
            from_number
            if from_number and from_number != "operator"
            else os.getenv("HODUPBX_DEFAULT_EXTENSION", "101")
        )
        ext_password_md5 = self.extension_password_md5 or os.getenv("HODUPBX_EXTENSION_PASSWORD_MD5", "")

        url = f"{self.base_url}/api/info/click2call"
        payload = {
            "token_id": self.token_id,
            "extension_number": ext_to_use,
            "extension_password": ext_password_md5,
            "phone_number": to_number,
        }
        print(f"[HoduPBX] click2call → POST {url}")
        print(f"[HoduPBX] payload: {payload}")
        try:
            # 30s timeout — HoduPBX holds connection open while ringing extension
            resp = requests.post(url, json=payload, timeout=30, verify=False)
            print(f"[HoduPBX] HTTP {resp.status_code} — body: {resp.text[:500]}")
            data = resp.json()
            if data.get("status") == "SUCCESS":
                call_uuid = str(data.get("data", f"hodu-{ext_to_use}-{to_number}"))
                print(f"[HoduPBX] Call initiated successfully. UUID: {call_uuid}")
                return call_uuid
            else:
                msg = data.get("message", "").strip()
                reason = data.get("status_reason", "")
                print(f"[HoduPBX] Non-SUCCESS: {data.get('status')} | {msg} | {reason}")
                # USER_BUSY means the extension already has an active call
                if "USER_BUSY" in msg or resp.status_code == 500:
                    raise RuntimeError(
                        f"Extension {ext_to_use} is busy — end the current call first. "
                        f"({msg})"
                    )
        except RuntimeError:
            raise  # re-raise USER_BUSY and similar for the view to handle
        except Exception as exc:
            print(f"[HoduPBX] Exception during click2call: {type(exc).__name__}: {exc}")
        return f"hodu-call-{ext_to_use}-{to_number}"

    def get_recording_url(self, call_id: str) -> str:
        """
        Page 48: HoduPBX recordingPath endpoint.
        Retrieves the .wav audio recording URL for a specific call ID.
        """
        import requests

        if not self.base_url or not self.token_id or not call_id:
            return ""

        url = f"{self.base_url}/api/info/TENANT/recordingPath"
        payload = {
            "token_id": self.token_id,
            "callid": str(call_id),
        }
        try:
            resp = requests.post(url, json=payload, timeout=5)
            data = resp.json()
            if data.get("status") == "SUCCESS":
                return data.get("data", {}).get("recordingPath", "")
        except Exception:
            pass
        return ""

    def get_active_calls(self) -> list:
        """
        Page 11: HoduPBX activeCalls endpoint.
        Returns live in-progress calls on HoduPBX.
        """
        import requests

        if not self.base_url or not self.token_id:
            return []

        url = f"{self.base_url}/api/info/TENANT/activeCalls"
        payload = {"token_id": self.token_id}
        try:
            resp = requests.post(url, json=payload, timeout=5)
            data = resp.json()
            if data.get("status") == "SUCCESS":
                return data.get("data", [])
        except Exception:
            pass
        return []

    def subscribe_extension(self, extension_number: str, extension_password: str) -> bool:
        """
        Page 9: HoduPBX crmSubscribe endpoint.
        Subscribes an extension to emit ringing callbacks when receiving inbound calls.
        """
        import requests

        if not self.base_url or not self.token_id:
            return False

        url = f"{self.base_url}/api/info/crmSubscribe"
        payload = {
            "token_id": self.token_id,
            "extension_number": str(extension_number),
            "extension_password": str(extension_password),
        }
        try:
            resp = requests.post(url, json=payload, timeout=5)
            data = resp.json()
            return data.get("status") == "SUCCESS"
        except Exception:
            return False


def get_telephony_provider() -> TelephonyProvider:
    providers = {
        "stub": StubTelephonyProvider,
        "hodupbx": HoduPBXProvider,
    }
    provider_cls = providers.get(settings.TELEPHONY_PROVIDER, StubTelephonyProvider)
    from apps.licensing.service import outbound_allowed

    if not outbound_allowed("ivr"):
        provider_cls = StubTelephonyProvider  # on-premise without the IVR feature: no PBX connection
    return provider_cls()

