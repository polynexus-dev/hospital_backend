"""Charging dispensed medicines to the patient's bill.

Inpatients: the admission's running bill. Everyone else: the patient's
draft pharmacy bill for the day (one per patient per day, reused until it
is finalised). The price is the batch MRP, which includes GST: the line
shows MRP / (1 + GST) with the GST added back, so the patient pays MRP.

A dispense is never blocked by billing: when the bill is on hold (or the
medicine is issued free) the dispense is recorded unbilled with the reason,
and can be billed later. A return of a billed dispense is credited on the
same bill while it is still open; a paid bill needs a credit note.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

SOURCE = "pharmacy"
RETURN_SOURCE = "pharmacy_return"
CENT = Decimal("0.01")


def unit_price(batch):
    rate = Decimal(batch.medicine.gst_rate or 0)
    return (Decimal(batch.mrp) / (1 + rate / 100)).quantize(CENT, rounding=ROUND_HALF_UP)


def _target_bill(record):
    from apps.billing.bed_charges import running_bill
    from apps.billing.models import Bill
    from apps.ipd.models import Admission

    admission = Admission.objects.filter(patient_id=record.patient_id, status=Admission.Status.ADMITTED).order_by("-admitted_at").first()
    if admission is not None:
        return running_bill(admission)
    bill = (
        Bill.objects.select_for_update()
        .filter(hospital_id=record.hospital_id, patient_id=record.patient_id, admission__isnull=True, status=Bill.Status.DRAFT,
                is_interim=False, created_at__date=timezone.localdate(), items__source=SOURCE)
        .distinct().order_by("-created_at").first()
    )
    return bill or Bill.objects.create(hospital_id=record.hospital_id, patient_id=record.patient_id, status=Bill.Status.DRAFT)


def _describe(batch):
    med = batch.medicine
    strength = f" {med.strength}" if med.strength else ""
    return f"{med.name}{strength} (batch {batch.batch_number})"[:255]


def _after_change(bill):
    from apps.billing.services import recalculate_bill

    recalculate_bill(bill)
    if bill.admission_id:
        from apps.schemes.services import reapply_for_admission

        reapply_for_admission(bill.admission)


@transaction.atomic
def bill_dispense(record):
    """Charges the dispense to the bill. Returns the BillItem, or None with
    record.billing_note saying why (no patient, bill on hold)."""
    from apps.billing.adjustments import BillOnHold, ensure_open
    from apps.billing.models import BillItem

    if record.bill_item_id:
        return record.bill_item
    if record.patient_id is None:
        record.billing_note = "Not billed: no patient (counter sale)."
        record.save(update_fields=["billing_note"])
        return None
    bill = _target_bill(record)
    try:
        ensure_open(bill, "add medicines")
    except BillOnHold:
        record.billing_note = f"Not billed: bill {bill.bill_number} is on hold ({bill.hold_reason})."
        record.save(update_fields=["billing_note"])
        return None
    batch = record.batch
    item = BillItem.objects.create(
        bill=bill, description=_describe(batch), quantity=record.quantity, unit_price=unit_price(batch), total_price=0,
        hsn_sac=batch.medicine.hsn_code, gst_rate=batch.medicine.gst_rate, service_date=timezone.localdate(record.dispensed_at),
        source=SOURCE, source_ref=f"dispense:{record.pk}",
    )
    _after_change(bill)
    record.bill_item, record.billing_note = item, ""
    record.save(update_fields=["bill_item", "billing_note"])
    return item


def mark_unbilled(record, note):
    record.billing_note = note[:255]
    record.save(update_fields=["billing_note"])


@transaction.atomic
def credit_return(ret):
    """Credits a patient return of a billed dispense on its bill."""
    from apps.billing.adjustments import BillOnHold, ensure_open
    from apps.billing.models import Bill, BillItem

    disp = ret.dispense_record
    if disp is None:
        return None
    if disp.batch_id != ret.batch_id:
        raise ValidationError({"dispense_record": "That dispense was from another batch."})
    already = sum(r.quantity for r in disp.returns.exclude(pk=ret.pk))
    if ret.quantity + already > disp.quantity:
        raise ValidationError({"quantity": f"Only {disp.quantity - already} of that dispense can still be returned."})
    item = disp.bill_item
    if item is None:
        return None
    bill = Bill.objects.select_for_update().get(pk=item.bill_id)
    if bill.status in (Bill.Status.PAID, Bill.Status.CANCELLED):
        ret.billing_note = f"Bill {bill.bill_number} is {bill.status}: refund with a credit note."
        ret.save(update_fields=["billing_note"])
        return None
    try:
        ensure_open(bill, "credit the return")
    except BillOnHold:
        ret.billing_note = f"Not credited: bill {bill.bill_number} is on hold."
        ret.save(update_fields=["billing_note"])
        return None
    credit = BillItem.objects.create(
        bill=bill, description=f"Return: {item.description}"[:255], quantity=ret.quantity, unit_price=-Decimal(item.unit_price), total_price=0,
        hsn_sac=item.hsn_sac, gst_rate=item.gst_rate, service_date=timezone.localdate(), source=RETURN_SOURCE, source_ref=f"return:{ret.pk}",
    )
    _after_change(bill)
    ret.credit_item, ret.billing_note = credit, ""
    ret.save(update_fields=["credit_item", "billing_note"])
    return credit
