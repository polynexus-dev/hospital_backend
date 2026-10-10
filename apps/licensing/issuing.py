"""Platform side: building and signing on-premise licenses."""
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from .crypto import load_private_key, seal


class SigningKeyMissing(Exception):
    pass


def signing_key():
    pem = settings.LICENSE_SIGNING_KEY
    if not pem and settings.LICENSE_SIGNING_KEY_PATH:
        pem = Path(settings.LICENSE_SIGNING_KEY_PATH).read_text()
    if not pem:
        raise SigningKeyMissing("License signing isn't configured (LICENSE_SIGNING_KEY).")
    return load_private_key(pem.replace("\\n", "\n"))


def next_license_id(hospital, now):
    from apps.saas_admin.models import OnPremiseLicense

    stem = f"LIC-{now:%Y}-{slugify(hospital.slug or hospital.name).upper()[:20]}"
    count = OnPremiseLicense.objects.filter(license_id__startswith=stem).count()
    return f"{stem}-{count + 1:03d}"


@transaction.atomic
def issue_license(hospital, *, issued_by, duration_days, enabled_modules, machine_fingerprint,
                  max_active_users=0, max_beds=0, grace_period_days=14, tier=""):
    from apps.saas_admin.models import OnPremiseLicense

    key = signing_key()
    now = timezone.now().replace(microsecond=0)
    expires_at = (now + timedelta(days=duration_days)).replace(hour=23, minute=59, second=59)
    payload = {
        "license_id": next_license_id(hospital, now),
        "hospital_id": str(hospital.pk),
        "hospital_name": hospital.name,
        "issued_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "grace_period_days": grace_period_days,
        "tier": tier,
        "enabled_modules": list(enabled_modules),
        "max_active_users": max_active_users,
        "max_beds": max_beds,
        "machine_fingerprint": machine_fingerprint,
    }
    record = OnPremiseLicense.objects.create(
        hospital=hospital, license_id=payload["license_id"], tier=tier, issued_at=now, expires_at=expires_at,
        grace_period_days=grace_period_days, enabled_modules=payload["enabled_modules"],
        max_active_users=max_active_users, max_beds=max_beds, machine_fingerprint=machine_fingerprint,
        payload=payload, license_file=seal(payload, key), issued_by=issued_by,
    )
    if not hospital.is_on_premise:
        hospital.is_on_premise = True
        hospital.save(update_fields=["is_on_premise"])
    return record
