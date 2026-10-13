"""Online payments through Razorpay payment links (NABH FPM.2.b, FPM.3.e).
Razorpay itself is mocked; the webhook signature check is real."""
import hashlib
import hmac
import json
from decimal import Decimal
from unittest import mock

import pytest

from apps.billing.models import Bill, OnlinePaymentLink, Payment, PaymentGatewayAccount
from apps.patients.models import Patient
from apps.portal.tests import _portal_client

SECRET = "whsec_test_123"


@pytest.fixture
def patient(hospital):
    return Patient.objects.create(hospital=hospital, first_name="Asha", last_name="Rao", mobile="9811100051")


@pytest.fixture
def bill(hospital, patient):
    return Bill.objects.create(hospital=hospital, patient=patient, total_amount=Decimal("1500"), net_amount=Decimal("1500"), status="issued")


@pytest.fixture
def gateway(auth_client):
    res = auth_client.put("/api/v1/billing/payment-gateway/", {"key_id": "rzp_test_abc", "key_secret": "secret_xyz", "webhook_secret": SECRET}, format="json")
    assert res.status_code == 200, res.json()
    return res.json()


def razorpay_ok(link_id="plink_1"):
    resp = mock.Mock(status_code=200, headers={"content-type": "application/json"})
    resp.json.return_value = {"id": link_id, "short_url": f"https://rzp.io/i/{link_id}", "status": "created"}
    return mock.patch("apps.billing.online_payments.requests.post", return_value=resp)


def webhook(api_client, hospital, body, secret=SECRET):
    raw = json.dumps(body).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return api_client.post(f"/api/v1/billing/payment-webhook/razorpay/{hospital.pk}/", data=raw, content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=sig)


def paid_event(link_id, amount_paise, method="upi", pay_id="pay_1"):
    return {"event": "payment_link.paid", "payload": {"payment_link": {"entity": {"id": link_id, "status": "paid"}},
                                                      "payment": {"entity": {"id": pay_id, "amount": amount_paise, "method": method}}}}


def test_gateway_settings_keep_secrets_write_only(auth_client, gateway):
    assert gateway["configured"] is True and gateway["source"] == "hospital" and "key_secret" not in gateway
    assert PaymentGatewayAccount.objects.get().key_secret == "secret_xyz"
    assert auth_client.put("/api/v1/billing/payment-gateway/", {"key_id": "bad"}, format="json").status_code == 400


def test_link_needs_a_configured_gateway(auth_client, bill):
    res = auth_client.post("/api/v1/billing/payment-links/", {"bill": bill.pk}, format="json")
    assert res.status_code == 503 and res.json()["code"] == "gateway_not_configured"


# NABH FPM.2.b, FPM.3.e
def test_payment_link_paid_by_upi_settles_the_bill(auth_client, api_client, hospital, bill, gateway, monkeypatch):
    sent = []
    monkeypatch.setattr("apps.clinical.notify.notify_patient", lambda p, purpose, ctx, **kw: sent.append((purpose, ctx)) or object())
    with razorpay_ok() as post:
        res = auth_client.post("/api/v1/billing/payment-links/", {"bill": bill.pk, "amount": "1000"}, format="json")
    assert res.status_code == 201, res.json()
    assert post.call_args.kwargs["json"]["amount"] == 100000 and post.call_args.kwargs["auth"] == ("rzp_test_abc", "secret_xyz")
    assert sent == [("payment_link", {"amount": "1000.00", "link": "https://rzp.io/i/plink_1"})]
    assert auth_client.post("/api/v1/billing/payment-links/", {"bill": bill.pk, "amount": "2000"}, format="json").status_code == 400  # over balance

    assert webhook(api_client, hospital, paid_event("plink_1", 100000), secret="forged").status_code == 400
    assert not Payment.objects.exists()
    assert webhook(api_client, hospital, paid_event("plink_1", 100000)).json() == {"status": "recorded"}
    assert webhook(api_client, hospital, paid_event("plink_1", 100000)).json() == {"status": "already recorded"}  # Razorpay retries
    pay = Payment.objects.get()
    assert (pay.amount, pay.payment_method, pay.transaction_id) == (Decimal("1000"), "upi", "pay_1")
    bill.refresh_from_db()
    assert bill.status == "partially_paid" and OnlinePaymentLink.objects.get().status == "paid"


# NABH FPM.2.b
def test_patient_pays_the_balance_from_the_portal(api_client, hospital, patient, bill, gateway, monkeypatch):
    c, _ = _portal_client(hospital, patient.mobile, monkeypatch)
    with razorpay_ok("plink_portal"):
        res = c.post(f"/api/v1/portal/bills/{bill.pk}/pay/", {}, format="json")
    assert res.status_code == 201 and res.json() == {"pay_url": "https://rzp.io/i/plink_portal", "amount": 1500.0}
    webhook(api_client, hospital, paid_event("plink_portal", 150000, method="card", pay_id="pay_2"))
    bill.refresh_from_db()
    assert bill.status == "paid" and Payment.objects.get().payment_method == "card"


def test_unpaid_link_can_be_cancelled(auth_client, bill, gateway):
    with razorpay_ok() as post:
        link = auth_client.post("/api/v1/billing/payment-links/", {"bill": bill.pk, "notify": False}, format="json").json()
        assert auth_client.post(f"/api/v1/billing/payment-links/{link['id']}/cancel/").json()["status"] == "cancelled"
    assert post.call_args.args[0].endswith("/payment_links/plink_1/cancel")
