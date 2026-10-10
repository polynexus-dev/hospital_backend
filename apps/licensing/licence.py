"""Building licence payloads — shared by the SaaS console (issuing.py) and
the offline issuer CLI (tools/license_issuer). Pure Python, no Django."""
import secrets
import uuid
from datetime import date, datetime, time, timezone

from .features import FEATURE_KEYS


def _utc(d: date, at: time) -> str:
    return datetime.combine(d, at, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def new_license_id(today: date | None = None) -> str:
    return f"LIC-{(today or date.today()):%Y}-{secrets.token_hex(4).upper()}"


def new_deployment_id() -> str:
    return str(uuid.uuid4())


def build_payload(*, customer_name: str, deployment_id: str, starts_on: date, expires_on: date,
                  grace_period_days: int = 14, max_active_users: int = 0, features=(), hardware_binding: bool = False,
                  machine_fingerprint: str = "", license_id: str | None = None, issued_at: datetime | None = None,
                  max_beds: int = 0, tier: str = "", hospital_id: str | None = None,
                  issued_by: str = "", approved_by: str = "") -> dict:
    """A licence payload, validated. Raises ValueError with a readable message."""
    if not customer_name.strip():
        raise ValueError("Customer name is required.")
    uuid.UUID(deployment_id)  # raises ValueError if malformed
    if expires_on <= starts_on:
        raise ValueError("The expiry date must be after the start date.")
    if grace_period_days < 0 or max_active_users < 0 or max_beds < 0:
        raise ValueError("Grace period and limits can't be negative.")
    unknown = [f for f in features if f not in FEATURE_KEYS]
    if unknown:
        raise ValueError(f"Unknown features: {', '.join(unknown)}")
    fingerprint = machine_fingerprint.strip().lower()
    if hardware_binding and fingerprint != "*" and (len(fingerprint) != 64 or any(c not in "0123456789abcdef" for c in fingerprint)):
        raise ValueError("The machine fingerprint must be the 64-character code the product prints.")

    issued = (issued_at or datetime.now(timezone.utc)).replace(microsecond=0)
    payload = {
        "license_id": license_id or new_license_id(issued.date()),
        "customer_name": customer_name.strip(),
        "hospital_name": customer_name.strip(),
        "deployment_id": deployment_id,
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
        "starts_at": _utc(starts_on, time.min),
        "expires_at": _utc(expires_on, time(23, 59, 59)),
        "grace_period_days": grace_period_days,
        "max_active_users": max_active_users,
        "max_beds": max_beds,
        "features": [f for f in FEATURE_KEYS if f in features],
        "hardware_binding": hardware_binding,
        "machine_fingerprint": fingerprint if hardware_binding else "",
        "tier": tier,
        # Staff codes of who requested and who approved it — signed, so
        # anyone holding the licence can see who issued it.
        "issued_by": issued_by,
        "approved_by": approved_by,
    }
    if hospital_id:
        payload["hospital_id"] = str(hospital_id)
    return payload


def summary(payload: dict) -> str:
    """Human-readable licence summary, for the customer."""
    from .features import label

    features = payload.get("features")
    lines = [
        f"Licence ID      : {payload.get('license_id')}",
        f"Licensed to     : {payload.get('customer_name') or payload.get('hospital_name')}",
        f"Deployment ID   : {payload.get('deployment_id') or '-'}",
        f"Valid from      : {str(payload.get('starts_at') or payload.get('issued_at'))[:10]}",
        f"Expires on      : {str(payload.get('expires_at'))[:10]}  (+{payload.get('grace_period_days', 0)} days grace)",
        f"Max active users: {payload.get('max_active_users') or 'unlimited'}",
        f"Issued by       : {payload.get('issued_by') or '-'}  (approved by {payload.get('approved_by') or '-'})",
        "Hardware binding: " + (f"yes ({payload.get('machine_fingerprint')})" if payload.get("hardware_binding", True) is not False else "no"),
        "Features        :",
    ]
    if features is None:
        lines.append(f"  modules: {', '.join(payload.get('enabled_modules') or []) or 'all'}")
    else:
        lines += [f"  [x] {label(f)}" for f in features] or ["  (none)"]
    return "\n".join(lines)
