"""License verification and enforcement for on-premise installations.

Everything here is a no-op in SaaS mode (DEPLOYMENT_MODE="saas"): the
subscription and tenant machinery in apps.saas_admin governs that instead.
"""
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from . import public_key as bundled
from .crypto import LicenseTampered, load_public_key, open_license
from .fingerprint import machine_fingerprint

VALID = "valid"
EXPIRING_SOON = "expiring_soon"
GRACE_PERIOD = "grace_period"
EXPIRED = "expired"
TAMPERED = "tampered"
INVALID_MACHINE = "invalid_machine"
MISSING = "missing"

EXPIRING_SOON_DAYS = 30
CLOCK_TOLERANCE = timedelta(hours=1)
REQUIRED_FIELDS = ("license_id", "hospital_id", "hospital_name", "issued_at", "expires_at", "machine_fingerprint")
ANY_MACHINE = "*"


@dataclass
class LicenseStatus:
    state: str
    message: str = ""
    payload: dict | None = None
    expires_at: datetime | None = None
    grace_ends_at: datetime | None = None
    days_left: int | None = None  # days until expires_at; negative once past it
    fingerprint: str = field(default_factory=str)

    @property
    def usable(self) -> bool:
        return self.state in (VALID, EXPIRING_SOON, GRACE_PERIOD)

    @property
    def read_only(self) -> bool:
        return not self.usable

    def as_dict(self, detail=False):
        data = {
            "mode": "on_premise",
            "state": self.state,
            "message": self.message,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "grace_ends_at": self.grace_ends_at.isoformat() if self.grace_ends_at else None,
            "days_left": self.days_left,
            "read_only": self.read_only,
        }
        if detail:
            p = self.payload or {}
            data.update({
                "license_id": p.get("license_id"),
                "hospital_name": p.get("hospital_name"),
                "tier": p.get("tier"),
                "issued_at": p.get("issued_at"),
                "enabled_modules": p.get("enabled_modules"),
                "max_active_users": p.get("max_active_users"),
                "max_beds": p.get("max_beds"),
                "licensed_fingerprint": p.get("machine_fingerprint"),
                "machine_fingerprint": self.fingerprint,
            })
        return data


def is_on_premise() -> bool:
    return getattr(settings, "DEPLOYMENT_MODE", "saas") == "on_premise"


def _public_key():
    if not bundled.PUBLIC_KEY_PEM:
        raise LicenseTampered("This installation was built without a license verification key.")
    return load_public_key(bundled.PUBLIC_KEY_PEM)


def _parse_time(value) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def license_path() -> Path:
    return Path(settings.LICENSE_FILE_PATH)


def read_license_file() -> str | None:
    try:
        return license_path().read_text().strip() or None
    except OSError:
        return None


def open_payload(blob: str) -> dict:
    """Signature-checked payload with its required fields. Raises LicenseTampered."""
    payload = open_license(blob, _public_key())
    missing = [f for f in REQUIRED_FIELDS if not payload.get(f)]
    if missing:
        raise LicenseTampered(f"The license is missing {', '.join(missing)}.")
    try:
        _parse_time(payload["issued_at"]), _parse_time(payload["expires_at"])
    except ValueError:
        raise LicenseTampered("The license dates are unreadable.")
    return payload


def machine_matches(payload: dict, fingerprint: str) -> bool:
    return payload.get("machine_fingerprint") in (ANY_MACHINE, fingerprint)


def verify_license(blob: str | None = None, *, now=None, check_clock=True, check_hospital=True) -> LicenseStatus:
    """Full verification of `blob` (default: the installed license file)."""
    fingerprint = machine_fingerprint()
    blob = blob if blob is not None else read_license_file()
    if not blob:
        return LicenseStatus(MISSING, "No license is installed. Upload a license file to activate this installation.", fingerprint=fingerprint)
    try:
        payload = open_payload(blob)
    except LicenseTampered as exc:
        return LicenseStatus(TAMPERED, str(exc), fingerprint=fingerprint)

    issued_at, expires_at = _parse_time(payload["issued_at"]), _parse_time(payload["expires_at"])
    grace_ends_at = expires_at + timedelta(days=int(payload.get("grace_period_days") or 0))
    status = LicenseStatus(VALID, payload=payload, expires_at=expires_at, grace_ends_at=grace_ends_at, fingerprint=fingerprint)

    if not machine_matches(payload, fingerprint):
        status.state, status.message = INVALID_MACHINE, "This license was issued for a different server."
        return status

    if check_hospital:
        from apps.core.models import Hospital

        if Hospital.objects.exists() and not Hospital.objects.filter(pk=payload["hospital_id"]).exists():
            status.state, status.message = TAMPERED, "This license was issued for a different hospital."
            return status

    now = now or timezone.now()
    if check_clock:
        from .models import LicenseClock

        previous = LicenseClock.advance(now)
        if previous is not None and now < previous - CLOCK_TOLERANCE:
            status.state, status.message = TAMPERED, "The system clock has been set back. Correct the server time."
            return status
    if now < issued_at - CLOCK_TOLERANCE:
        status.state, status.message = TAMPERED, "The system clock is earlier than the license issue date. Correct the server time."
        return status

    status.days_left = (expires_at - now).days
    if now <= expires_at:
        if expires_at - now <= timedelta(days=EXPIRING_SOON_DAYS):
            status.state, status.message = EXPIRING_SOON, f"The license expires in {status.days_left} days."
    elif now <= grace_ends_at:
        status.state = GRACE_PERIOD
        status.message = f"The license has expired. The system becomes read-only in {(grace_ends_at - now).days} days."
    else:
        status.state, status.message = EXPIRED, "License expired. System in read-only emergency archive mode."
    return status


def _cache_key() -> str:
    try:
        st = license_path().stat()
        return f"licensing:status:{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return "licensing:status:none"


def current_status() -> LicenseStatus:
    """The installed license's status, cached for LICENSE_CACHE_SECONDS. The
    cache key follows the file, so installing a new license applies at once."""
    key = _cache_key()
    cached = cache.get(key)
    if cached is not None:
        return LicenseStatus(**cached)
    status = verify_license()
    cache.set(key, asdict(status), getattr(settings, "LICENSE_CACHE_SECONDS", 600))
    return status


def licensed_modules():
    """Module keys the license allows, or None for no license-level limit
    (SaaS mode, or no readable license — then everything is read-only anyway)."""
    if not is_on_premise():
        return None
    payload = current_status().payload
    if not payload or not payload.get("enabled_modules"):
        return None
    return list(payload["enabled_modules"])


class CapacityExceeded(Exception):
    pass


def check_capacity(kind: str, current_count: int, adding: int = 1) -> None:
    """Raises CapacityExceeded when the license caps `kind` ("users" / "beds")."""
    if not is_on_premise():
        return
    payload = current_status().payload or {}
    limit = payload.get({"users": "max_active_users", "beds": "max_beds"}[kind])
    if limit and current_count + adding > int(limit):
        noun = "active users" if kind == "users" else "beds"
        raise CapacityExceeded(f"The license allows {limit} {noun}. Contact support to raise the limit.")


def install_license(blob: str) -> LicenseStatus:
    """Verifies `blob` and, if usable, writes it as the installed license."""
    blob = blob.strip()
    status = verify_license(blob)
    if not status.usable:
        return status
    current = verify_license(check_clock=False) if read_license_file() else None
    if current and current.payload and current.usable and _parse_time(status.payload["issued_at"]) < _parse_time(current.payload["issued_at"]):
        status.state, status.message = TAMPERED, "This license is older than the one already installed."
        return status

    path = license_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(blob + "\n")
    os.replace(tmp, path)
    cache.delete(_cache_key())
    return status


def check_machine_binding() -> None:
    """Called as the server boots: refuse to start on a machine the
    installed license wasn't issued for. A missing or expired license still
    boots (read-only), so a new license can be uploaded."""
    if not is_on_premise():
        return
    if not bundled.PUBLIC_KEY_PEM:
        raise RuntimeError("On-premise build has no license verification key (apps/licensing/public_key.py).")
    blob = read_license_file()
    if not blob:
        return
    try:
        payload = open_payload(blob)
    except LicenseTampered:
        return  # reported as TAMPERED (read-only) once running
    fingerprint = machine_fingerprint()
    if not machine_matches(payload, fingerprint):
        raise RuntimeError(
            f"License {payload['license_id']} is bound to a different server. "
            f"This server's fingerprint is {fingerprint} — send it to support for a new license."
        )
