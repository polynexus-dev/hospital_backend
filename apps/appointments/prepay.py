"""Book and pay in one step from the patient portal.

The hospital chooses (Finance > Front desk policy > portal_payment):
- at_hospital: as before, nothing to pay online;
- optional: the booking is confirmed and the patient is offered a payment link;
- required: the slot is held for portal_payment_hold_minutes while the
  patient pays; Razorpay's webhook confirms it (on_bill_paid), and an unpaid
  hold is released by release_expired_holds (every 5 minutes) so the slot can
  be booked again.

The fee comes from the same consultation-fee rules as the front desk
(build_charges), so follow-up pricing and free follow-ups apply. When the
patient arrives, the front desk sees the consultation as already paid and
doesn't charge it again (frontdesk.register_opd_visit).
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Appointment

logger = logging.getLogger(__name__)


def portal_charges(patient, doctor):
    from apps.finance.models import FrontDeskPolicy

    from .frontdesk import build_charges

    policy = FrontDeskPolicy.for_hospital(patient.hospital_id)
    from apps.hr.staff_rate import default_category

    sheet = build_charges(hospital_id=patient.hospital_id, patient=patient, doctor=doctor, rate_category=default_category(patient, policy.default_rate_category), is_new_patient=False)
    return policy, sheet


@transaction.atomic
def _prepaid_bill(appointment, sheet):
    from apps.billing.models import Bill, BillItem
    from apps.billing.services import recalculate_bill

    bill = Bill.objects.create(hospital_id=appointment.hospital_id, patient=appointment.patient, status=Bill.Status.UNPAID)
    for c in sheet.charges:
        BillItem.objects.create(bill=bill, description=c.description[:255], quantity=1, unit_price=c.amount, tariff=c.tariff, doctor=c.doctor,
                                hsn_sac=getattr(c.tariff, "hsn_sac", "") or "", gst_rate=getattr(c.tariff, "gst_rate", 0) or 0,
                                service_date=appointment.slot.date, source=c.source, source_ref=f"appointment:{appointment.pk}")
    recalculate_bill(bill)
    return bill


def offer_payment(appointment):
    """After a portal booking. Returns a dict for the response: what's due,
    the payment link, and the hold (or why online payment isn't offered)."""
    from apps.billing import online_payments as gw
    from apps.finance.models import FrontDeskPolicy

    policy, sheet = portal_charges(appointment.patient, appointment.doctor)
    if policy.portal_payment == FrontDeskPolicy.PortalPayment.AT_HOSPITAL or sheet.total <= 0:
        return {"payment": "at_hospital", "amount": float(sheet.total)}
    try:
        gw.credentials_for(appointment.hospital_id)
    except gw.GatewayNotConfigured:
        return {"payment": "at_hospital", "amount": float(sheet.total), "note": "Online payment isn't available yet; please pay at the hospital."}
    bill = _prepaid_bill(appointment, sheet)
    required = policy.portal_payment == FrontDeskPolicy.PortalPayment.REQUIRED
    try:
        link = gw.create_payment_link(bill, channel="portal", notify=False)
    except gw.GatewayError as exc:
        logger.warning("Payment link for appointment %s failed: %s", appointment.pk, exc)
        return {"payment": "at_hospital", "amount": float(sheet.total), "note": "Online payment failed to start; please pay at the hospital."}
    appointment.prepaid_bill = bill
    appointment.payment_hold_until = timezone.now() + timedelta(minutes=policy.portal_payment_hold_minutes) if required else None
    appointment.save(update_fields=["prepaid_bill", "payment_hold_until"])
    return {"payment": "required" if required else "optional", "amount": float(link.amount), "pay_url": link.short_url,
            "hold_until": appointment.payment_hold_until}


def on_bill_paid(bill):
    """Called by the payment webhook once a bill is fully paid."""
    from apps.clinical.notify import notify_patient

    for appt in Appointment.objects.filter(prepaid_bill=bill, status=Appointment.Status.BOOKED).select_related("doctor", "slot", "patient"):
        held = appt.payment_hold_until is not None
        Appointment.objects.filter(pk=appt.pk).update(payment_hold_until=None)
        notify_patient(appt.patient, "booking_paid", {
            "doctor_name": appt.doctor.name, "date": appt.slot.date.strftime("%d %b %Y"), "time": appt.slot.start_time.strftime("%H:%M"),
            "amount": f"{bill.net_amount:.2f}",
        })
        logger.info("Portal booking %s paid%s", appt.pk, " (hold cleared)" if held else "")


def release_expired_holds(now=None) -> int:
    """Unpaid 'payment required' bookings past their hold: cancel them so
    the slot can be booked again, and stop the payment link."""
    from apps.billing import online_payments as gw
    from apps.billing.models import Bill, OnlinePaymentLink

    from .services import cancel

    now = now or timezone.now()
    released = 0
    for appt in Appointment.objects.filter(status=Appointment.Status.BOOKED, payment_hold_until__lt=now).select_related("prepaid_bill", "patient"):
        bill = appt.prepaid_bill
        if bill is not None and bill.status == Bill.Status.PAID:
            Appointment.objects.filter(pk=appt.pk).update(payment_hold_until=None)  # paid; webhook was just late
            continue
        cancel(appt)
        for link in OnlinePaymentLink.objects.filter(bill=bill, status=OnlinePaymentLink.Status.CREATED) if bill else []:
            try:
                gw.cancel_payment_link(link)
            except gw.GatewayError:
                logger.warning("Couldn't cancel payment link %s", link.pk)
        if bill is not None and not bill.payments.exists():
            Bill.objects.filter(pk=bill.pk).update(status=Bill.Status.CANCELLED)
        released += 1
    return released
