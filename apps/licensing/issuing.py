"""Platform side: building and signing on-premise licenses (SaaS console).
The offline CLI in tools/license_issuer builds the same payload."""
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from .crypto import load_private_key, seal
from .features import modules_for
from .licence import build_payload, new_deployment_id


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
def issue_license(hospital, *, issued_by, duration_days, features, machine_fingerprint="", hardware_binding=True,
                  deployment_id="", max_active_users=0, max_beds=0, grace_period_days=14, tier=""):
    from apps.saas_admin.models import OnPremiseLicense

    key = signing_key()
    now = timezone.now().replace(microsecond=0)
    payload = build_payload(
        customer_name=hospital.name,
        deployment_id=deployment_id or new_deployment_id(),
        starts_on=now.date(),
        expires_on=(now + timedelta(days=duration_days)).date(),
        grace_period_days=grace_period_days,
        max_active_users=max_active_users,
        max_beds=max_beds,
        features=features,
        hardware_binding=hardware_binding,
        machine_fingerprint=machine_fingerprint,
        license_id=next_license_id(hospital, now),
        issued_at=now,
        tier=tier,
        hospital_id=hospital.pk,
    )
    record = OnPremiseLicense.objects.create(
        hospital=hospital, license_id=payload["license_id"], tier=tier, issued_at=now,
        expires_at=payload["expires_at"].replace("Z", "+00:00"),
        grace_period_days=grace_period_days, features=payload["features"], deployment_id=payload["deployment_id"],
        enabled_modules=modules_for(payload["features"]),
        max_active_users=max_active_users, max_beds=max_beds,
        machine_fingerprint=payload["machine_fingerprint"] or "-",
        payload=payload, license_file=seal(payload, key), issued_by=issued_by,
    )
    record.refresh_from_db()
    if not hospital.is_on_premise:
        hospital.is_on_premise = True
        hospital.save(update_fields=["is_on_premise"])
    return record
