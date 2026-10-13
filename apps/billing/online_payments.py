"""Online payments through Razorpay payment links (NABH FPM.2.b, FPM.3.e).

Staff create a link for a bill's balance (sent to the patient by WhatsApp /
SMS), or the patient opens one from the portal. The patient pays by UPI,
card, netbanking or wallet on Razorpay's page; Razorpay calls our webhook,
which is checked against the hospital's webhook secret before a Payment is
recorded. Card and UPI details never touch our servers.

Each hospital uses its own Razorpay account (Settings -> Online payments),
so money settles straight into the hospital's bank account. An on-premise
install can instead set RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET /
RAZORPAY_WEBHOOK_SECRET in the .env.
"""
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from decimal import Decimal

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Bill, OnlinePaymentLink, Payment, PaymentGatewayAccount

logger = logging.getLogger(__name__)

API = "https://api.razorpay.com/v1"
METHODS = {"upi": "upi", "card": "card", "emi": "card", "netbanking": "bank_transfer", "wallet": "wallet"}


class GatewayError(Exception):
    pass


class GatewayNotConfigured(GatewayError):
    pass


@dataclass
class Credentials:
    key_id: str
    key_secret: str
    webhook_secret: str


def credentials_for(hospital_id) -> Credentials:
    acct = PaymentGatewayAccount.objects.filter(hospital_id=hospital_id, is_active=True).first()
    if acct and acct.key_id and acct.key_secret:
        return Credentials(acct.key_id, acct.key_secret, acct.webhook_secret)
    key_id, secret = getattr(settings, "RAZORPAY_KEY_ID", ""), getattr(settings, "RAZORPAY_KEY_SECRET", "")
    if key_id and secret:
        return Credentials(key_id, secret, getattr(settings, "RAZORPAY_WEBHOOK_SECRET", ""))
    raise GatewayNotConfigured("Online payments aren't set up. Add the hospital's Razorpay keys in Settings -> Online payments.")


def bill_balance(bill) -> Decimal:
    paid = sum((p.amount for p in bill.payments.all()), Decimal("0"))
    return max(Decimal(bill.net_amount) - paid, Decimal("0"))


def refresh_bill_status(bill):
    paid = sum((p.amount for p in bill.payments.all()), Decimal("0"))
    bill.status = Bill.Status.PAID if paid >= bill.net_amount else Bill.Status.PARTIALLY_PAID
    bill.save(update_fields=["status"])


def create_payment_link(bill, *, amount=None, user=None, channel="staff", notify=True) -> OnlinePaymentLink:
    if bill.status == Bill.Status.CANCELLED:
        raise GatewayError("This bill is cancelled.")
    balance = bill_balance(bill)
    amount = Decimal(str(amount)) if amount not in (None, "") else balance
    if amount <= 0 or amount > balance:
        raise GatewayError(f"Amount must be between 1 and the balance of Rs. {balance}.")
    existing = OnlinePaymentLink.objects.filter(bill=bill, amount=amount, status=OnlinePaymentLink.Status.CREATED).first()
    if existing:
        return existing
    creds = credentials_for(bill.hospital_id)
    patient = bill.patient
    body = {
        "amount": int(amount * 100), "currency": "INR", "accept_partial": False,
        "reference_id": f"BILL-{bill.pk}-{timezone.now():%y%m%d%H%M%S}",
        "description": f"{bill.hospital.name} bill {getattr(bill, 'bill_number', '') or bill.pk}"[:2048],
        "customer": {"name": patient.full_name[:100], "contact": patient.mobile or ""},
        "notify": {"sms": False, "email": False},  # we send it ourselves, on our templates
        "expire_by": int((timezone.now() + timezone.timedelta(days=7)).timestamp()),
        "notes": {"hospital_id": str(bill.hospital_id), "bill_id": str(bill.pk)},
    }
    try:
        resp = requests.post(f"{API}/payment_links", json=body, auth=(creds.key_id, creds.key_secret), timeout=20)
    except requests.RequestException as exc:
        raise GatewayError(f"Couldn't reach Razorpay: {exc}") from exc
    if resp.status_code >= 400:
        detail = (resp.json().get("error") or {}).get("description") if resp.headers.get("content-type", "").startswith("application/json") else resp.text[:200]
        raise GatewayError(f"Razorpay refused the link: {detail}")
    data = resp.json()
    link = OnlinePaymentLink.objects.create(
        hospital_id=bill.hospital_id, bill=bill, amount=amount, provider_link_id=data["id"], short_url=data["short_url"],
        channel=channel, created_by=user,
    )
    if notify:
        from apps.clinical.notify import notify_patient

        if notify_patient(patient, "payment_link", {"amount": f"{amount:.2f}", "link": link.short_url}) is not None:
            OnlinePaymentLink.objects.filter(pk=link.pk).update(patient_notified_at=timezone.now())
    return link


def cancel_payment_link(link):
    if link.status != OnlinePaymentLink.Status.CREATED:
        raise GatewayError("Only an unpaid link can be cancelled.")
    creds = credentials_for(link.hospital_id)
    try:
        requests.post(f"{API}/payment_links/{link.provider_link_id}/cancel", auth=(creds.key_id, creds.key_secret), timeout=20)
    except requests.RequestException as exc:
        raise GatewayError(f"Couldn't reach Razorpay: {exc}") from exc
    link.status = OnlinePaymentLink.Status.CANCELLED
    link.save(update_fields=["status"])


def verify_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    if not (secret and signature):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def handle_webhook(hospital_id, raw_body: bytes, signature: str):
    """Returns (http_status, message). Idempotent: Razorpay retries."""
    try:
        creds = credentials_for(hospital_id)
    except GatewayNotConfigured:
        return 404, "not configured"
    if not verify_signature(raw_body, signature, creds.webhook_secret):
        logger.warning("Rejected a Razorpay webhook with a bad signature for hospital %s", hospital_id)
        return 400, "bad signature"
    event = json.loads(raw_body or b"{}")
    kind = event.get("event", "")
    entity = (event.get("payload", {}).get("payment_link") or {}).get("entity") or {}
    link = OnlinePaymentLink.objects.filter(hospital_id=hospital_id, provider_link_id=entity.get("id", "")).select_related("bill").first()
    if link is None:
        return 200, "ignored"
    if kind in ("payment_link.expired", "payment_link.cancelled") and link.status == OnlinePaymentLink.Status.CREATED:
        link.status = OnlinePaymentLink.Status.EXPIRED if kind.endswith("expired") else OnlinePaymentLink.Status.CANCELLED
        link.save(update_fields=["status"])
        return 200, "updated"
    if kind != "payment_link.paid":
        return 200, "ignored"
    pay = (event["payload"].get("payment") or {}).get("entity") or {}
    with transaction.atomic():
        link = OnlinePaymentLink.objects.select_for_update().get(pk=link.pk)
        if link.status == OnlinePaymentLink.Status.PAID:
            return 200, "already recorded"
        amount = Decimal(pay.get("amount", int(link.amount * 100))) / 100
        payment = Payment.objects.create(
            hospital_id=link.hospital_id, bill=link.bill, amount=amount,
            payment_method=METHODS.get(pay.get("method", ""), Payment.PaymentMethod.BANK_TRANSFER),
            transaction_id=pay.get("id", link.provider_link_id)[:100],
        )
        link.status, link.payment, link.paid_at = OnlinePaymentLink.Status.PAID, payment, timezone.now()
        link.save(update_fields=["status", "payment", "paid_at"])
        refresh_bill_status(link.bill)
    return 200, "recorded"
