"""Usage reports: what an offline, on-premise installation actually uses,
for the hospital to send to Polynexus at renewal.

Counts only (users, beds, patients registered, appointments) — never a
patient record, name or identifier. The report is readable JSON, so the
hospital can see exactly what it's sending, plus a `seal`: an HMAC over the
canonical report, keyed from the licence public key. The SaaS side checks it
on upload, which catches edits made to the file after it was generated. (The
key ships in the product, so it deters casual edits; it isn't proof against
someone who reverse-engineers the compiled code.)
"""
import hashlib
import hmac
import json
from datetime import timedelta

from cryptography.hazmat.primitives import serialization

REPORT_VERSION = 1


def _seal_key(public_key) -> bytes:
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(b"hms-usage-report:" + raw).digest()


def _canonical(report: dict) -> bytes:
    return json.dumps(report, sort_keys=True, separators=(",", ":")).encode()


def seal(report: dict, public_key) -> str:
    return hmac.new(_seal_key(public_key), _canonical(report), hashlib.sha256).hexdigest()


def verify(document: dict, public_key) -> bool:
    report, given = document.get("report"), document.get("seal")
    if not isinstance(report, dict) or not isinstance(given, str):
        return False
    return hmac.compare_digest(seal(report, public_key), given)


def build_report():
    """The usage report for this installation (on-premise)."""
    from django.conf import settings
    from django.utils import timezone

    from apps.accounts.models import User
    from apps.appointments.models import Appointment
    from apps.core.models import Hospital
    from apps.facilities.models import Bed
    from apps.patients.models import Patient

    from .service import current_status, licence_features, public_key_obj

    status = current_status()
    payload = status.payload or {}
    now = timezone.now()
    month_ago = now - timedelta(days=30)
    report = {
        "report_version": REPORT_VERSION,
        "generated_at": now.replace(microsecond=0).isoformat(),
        "app_version": getattr(settings, "APP_VERSION", "dev"),
        "licence": {
            "license_id": payload.get("license_id"),
            "deployment_id": payload.get("deployment_id") or getattr(settings, "DEPLOYMENT_ID", ""),
            "state": status.state,
            "expires_at": payload.get("expires_at"),
            "max_active_users": payload.get("max_active_users"),
            "max_beds": payload.get("max_beds"),
            "features": licence_features(payload) if payload else None,
        },
        "hospitals": [h.name for h in Hospital.objects.order_by("created_at")],
        "usage": {
            "active_users": User.objects.filter(hospital__isnull=False, is_active=True).count(),
            "total_users": User.objects.filter(hospital__isnull=False).count(),
            "beds": Bed._base_manager.count(),
            "patients_registered": Patient._base_manager.filter(is_deleted=False).count(),
            "patients_registered_last_30_days": Patient._base_manager.filter(is_deleted=False, created_at__gte=month_ago).count(),
            "appointments_last_30_days": Appointment._base_manager.filter(created_at__gte=month_ago).count(),
        },
    }
    return {"report": report, "seal": seal(report, public_key_obj())}
