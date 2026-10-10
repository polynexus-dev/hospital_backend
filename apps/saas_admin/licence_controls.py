"""Controls around issuing on-premise licences, so none can be issued or sold
without the company knowing:

  - only SaaS Owners, and staff an Owner has authorised, may request one;
  - every request needs a SaaS Owner's approval (two-person rule), and both
    steps need a fresh 2FA code, so a stolen password or session isn't enough;
  - the requester's and approver's staff codes are signed into the licence;
  - Owners are told about every request and every issued licence;
  - revocations ship as a signed list inside each new release;
  - a usage report naming a licence this console never issued raises a
    critical security alert.
"""
import logging
from datetime import timedelta

import pyotp
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

logger = logging.getLogger(__name__)


def otp_error(user, code) -> str | None:
    """Why `code` isn't a valid, current 2FA code for `user` (None if it is)."""
    if not user.is_2fa_enabled or not user.totp_secret:
        return "Turn on two-factor authentication for your account before issuing licences."
    if not code or not pyotp.TOTP(user.totp_secret).verify(str(code).strip(), valid_window=1):
        return "Enter the current 6-digit code from your authenticator app."
    return None


def saas_owners():
    from apps.accounts.models import User

    return User.objects.filter(is_saas_admin=True, is_active=True, is_blocked=False, saas_role=User.SaaSRole.OWNER)


def notify_owners(subject: str, body: str) -> None:
    """Email every SaaS Owner. Never blocks the action if mail fails."""
    recipients = [u.email for u in saas_owners()]
    if not recipients:
        return
    try:
        send_mail(f"[Polynexus licences] {subject}", body, getattr(settings, "DEFAULT_FROM_EMAIL", None), recipients, fail_silently=True)
    except Exception:  # noqa: BLE001 — a notification must never undo an approval
        logger.exception("Couldn't notify SaaS Owners: %s", subject)


def describe(params: dict, hospital) -> str:
    return (
        f"Hospital: {hospital.name}\n"
        f"Features: {', '.join(params.get('features') or [])}\n"
        f"Users: {params.get('max_users') or 'unlimited'}   Beds: {params.get('max_beds') or 'unlimited'}\n"
        f"Duration: {params.get('duration_days')} days\n"
        f"Machine: {params.get('machine_fingerprint') or 'any'}\n"
    )


def has_paid_invoice(record) -> bool:
    """Whether the hospital has paid for the period this licence covers — a
    paid platform invoice ending no earlier than 30 days before issue."""
    from .models import TenantInvoice

    return TenantInvoice.objects.filter(
        hospital_id=record.hospital_id, status=TenantInvoice.Status.PAID,
        billing_period_end__gte=(record.issued_at - timedelta(days=30)).date(),
    ).exists()


def revocation_document() -> str:
    """The signed list of revoked licence IDs, to build into the next release
    (save as Backend/apps/licensing/revocations.lic before `make bundle`)."""
    from apps.licensing.crypto import seal
    from apps.licensing.issuing import signing_key

    from .models import OnPremiseLicense

    revoked = sorted(OnPremiseLicense.objects.filter(revoked_at__isnull=False).values_list("license_id", flat=True))
    return seal({"type": "revocations", "generated_at": timezone.now().replace(microsecond=0).isoformat(), "revoked": revoked}, signing_key())
