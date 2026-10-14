"""Book and pay in one step from the patient portal."""
import datetime
import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.utils import timezone

from apps.appointments.models import Appointment, Doctor, Slot
from apps.appointments.prepay import release_expired_holds
from apps.billing.models import Bill, PaymentGatewayAccount
from apps.finance.models import ConsultationFeeRule, FrontDeskPolicy, ServiceTariff
from apps.patients.models import Patient
from apps.portal.tests import _portal_client

SECRET = "whsec_prepay"


@pytest.fixture
def setup(hospital):
    doctor = Doctor.objects.create(hospital=hospital, name="Rao")
    tariff = ServiceTariff.objects.create(hospital=hospital, name="Consultation", code="CONS", rate=Decimal("600"))
    ConsultationFeeRule.objects.create(hospital=hospital, new_visit_tariff=tariff)
    PaymentGatewayAccount.objects.create(hospital=hospital, key_id="rzp_test_x", key_secret="s", webhook_secret=SECRET)
    patient = Patient.objects.create(hospital=hospital, first_name="Asha", mobile="9811100091")
    slot = Slot.objects.create(hospital=hospital, doctor=doctor, date=datetime.date.today() + timedelta(days=1), start_time=datetime.time(10, 0), end_time=datetime.time(10, 15))
    return doctor, patient, slot


def policy(hospital, mode):
    p = FrontDeskPolicy.for_hospital(hospital.pk)
    p.portal_payment = mode
    p.save()


def razorpay(link_id="plink_book"):
    resp = mock.Mock(status_code=200, headers={"content-type": "application/json"})
    resp.json.return_value = {"id": link_id, "short_url": f"https://rzp.io/i/{link_id}", "status": "created"}
    return mock.patch("apps.billing.online_payments.requests.post", return_value=resp)


def book(c, patient, slot):
    return c.post("/api/v1/portal/appointments/", {"slot": slot.pk, "patient": patient.pk}, format="json")


def paid(api_client, hospital, link_id, paise):
    raw = json.dumps({"event": "payment_link.paid", "payload": {"payment_link": {"entity": {"id": link_id}},
                                                                 "payment": {"entity": {"id": "pay_1", "amount": paise, "method": "upi"}}}}).encode()
    sig = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return api_client.post(f"/api/v1/billing/payment-webhook/razorpay/{hospital.pk}/", data=raw, content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=sig)


def test_pay_at_hospital_is_unchanged(hospital, setup, monkeypatch):
    doctor, patient, slot = setup
    c, _ = _portal_client(hospital, patient.mobile, monkeypatch)
    res = book(c, patient, slot).json()
    assert res["payment"] == "at_hospital" and res["amount"] == 600.0 and "pay_url" not in res


def test_required_payment_holds_the_slot_then_confirms(api_client, hospital, setup, monkeypatch):
    doctor, patient, slot = setup
    policy(hospital, "required")
    c, _ = _portal_client(hospital, patient.mobile, monkeypatch)
    sent = []
    monkeypatch.setattr("apps.clinical.notify.notify_patient", lambda p, purpose, ctx, **kw: sent.append(purpose) or object())
    with razorpay():
        res = book(c, patient, slot).json()
    assert res["payment"] == "required" and res["pay_url"] == "https://rzp.io/i/plink_book" and res["hold_until"]
    appt = Appointment.objects.get()
    assert appt.payment_hold_until and appt.prepaid_bill.net_amount == Decimal("600")

    assert paid(api_client, hospital, "plink_book", 60000).status_code == 200
    appt.refresh_from_db()
    assert appt.payment_hold_until is None and appt.prepaid_bill.status == "paid" and "booking_paid" in sent
    assert release_expired_holds(timezone.now() + timedelta(hours=1)) == 0  # paid: nothing to release


def test_unpaid_hold_is_released_so_the_slot_can_be_booked_again(hospital, setup, monkeypatch):
    doctor, patient, slot = setup
    policy(hospital, "required")
    c, _ = _portal_client(hospital, patient.mobile, monkeypatch)
    with razorpay() as post:
        book(c, patient, slot)
        assert release_expired_holds(timezone.now() + timedelta(minutes=16)) == 1
    assert post.call_args.args[0].endswith("/payment_links/plink_book/cancel")
    appt = Appointment.objects.get()
    assert appt.status == "cancelled" and Bill.objects.get().status == "cancelled"
    other = Patient.objects.create(hospital=hospital, first_name="Kiran", mobile="9811100092")
    c2, _ = _portal_client(hospital, other.mobile, monkeypatch)
    assert book(c2, other, slot).status_code == 201


def test_optional_payment_confirms_at_once_and_offers_a_link(hospital, setup, monkeypatch):
    doctor, patient, slot = setup
    policy(hospital, "optional")
    c, _ = _portal_client(hospital, patient.mobile, monkeypatch)
    with razorpay():
        res = book(c, patient, slot).json()
    assert res["payment"] == "optional" and res["pay_url"] and res["hold_until"] is None
    assert release_expired_holds(timezone.now() + timedelta(days=1)) == 0


def test_no_gateway_falls_back_to_paying_at_the_hospital(hospital, setup, monkeypatch):
    doctor, patient, slot = setup
    policy(hospital, "required")
    PaymentGatewayAccount.objects.all().delete()
    c, _ = _portal_client(hospital, patient.mobile, monkeypatch)
    res = book(c, patient, slot).json()
    assert res["payment"] == "at_hospital" and "pay at the hospital" in res["note"]
    assert Appointment.objects.get().payment_hold_until is None


def test_front_desk_does_not_charge_a_prepaid_consultation_again(api_client, auth_client, hospital, setup, monkeypatch):
    doctor, patient, slot = setup
    policy(hospital, "required")
    Slot.objects.filter(pk=slot.pk).update(date=datetime.date.today())
    c, _ = _portal_client(hospital, patient.mobile, monkeypatch)
    with razorpay():
        book(c, patient, slot)
    paid(api_client, hospital, "plink_book", 60000)
    res = auth_client.post("/api/v1/frontdesk/registrations/", {"patient_id": patient.pk, "visit": {"doctor": doctor.pk}, "billing": {"settlement": "receipt", "payments": []}}, format="json")
    assert res.status_code in (200, 201), res.json()
    body = res.json()
    assert any("already paid online" in w for w in body.get("warnings", []))
    assert Bill.objects.filter(patient=patient).count() == 1  # only the online one
