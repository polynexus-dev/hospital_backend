"""Bill hold, re-pricing on a category change, and credit notes.

Hold: a bill put on hold (with a reason) refuses new charges, payments,
payment links, discounts, credit notes, re-pricing, interim bills and the
admission's discharge until someone with billing.hold_bill releases it.

Category change: re-prices the bill's tariff lines at the new category's
rate. Allowed until the bill is fully paid (then use a credit note).

Credit note: credits services on the bill — the amounts you give per line,
or one amount spread across the bill's services in proportion to their
value. Negative lines are added (so GST, totals and the ledger follow);
whatever has already been paid beyond the new total is refunded or kept
as the patient's advance.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Bill, BillCreditNote, BillHoldEvent, BillItem, Payment, PatientAdvance
from .services import recalculate_bill

CENT = Decimal("0.01")
ZERO = Decimal("0")
NOT_REPRICED = ("credit_note", "scheme_package", "scheme_adjustment", "package", "interim")
REFUND_METHODS = ("cash", "upi", "bank_transfer", "cheque", "card")


def q(x):
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


class BillOnHold(ValidationError):
    pass


def ensure_open(bill, doing="change this bill"):
    if bill.on_hold:
        raise BillOnHold({"detail": f"Bill {bill.bill_number} is on hold ({bill.hold_reason}); release it before you {doing}."})


def ensure_admission_open(admission, doing="discharge the patient"):
    held = Bill.objects.filter(admission=admission, on_hold=True).first()
    if held is not None:
        ensure_open(held, doing)


def hold(bill, user, reason):
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why the bill is on hold."})
    if bill.on_hold:
        raise ValidationError({"detail": "The bill is already on hold."})
    if bill.status == Bill.Status.CANCELLED:
        raise ValidationError({"detail": "The bill is cancelled."})
    bill.on_hold, bill.hold_reason, bill.held_by, bill.held_at = True, reason[:255], user, timezone.now()
    bill.save(update_fields=["on_hold", "hold_reason", "held_by", "held_at"])
    BillHoldEvent.objects.create(hospital_id=bill.hospital_id, bill=bill, action="hold", reason=reason[:255], by=user)
    return bill


def release(bill, user, note):
    if not bill.on_hold:
        raise ValidationError({"detail": "The bill isn't on hold."})
    note = (note or "").strip()
    if not note:
        raise ValidationError({"note": "Note how the hold was resolved."})
    bill.on_hold, bill.hold_reason = False, ""
    bill.save(update_fields=["on_hold", "hold_reason"])
    BillHoldEvent.objects.create(hospital_id=bill.hospital_id, bill=bill, action="release", reason=note[:255], by=user)
    return bill


# --- category change ------------------------------------------------------------

CATEGORIES = ("general", "private", "insurance", "corporate")


def reprice_preview(bill, category):
    """[(item, old_price, new_price)] for the tariff lines that would change."""
    out = []
    for item in bill.items.select_related("tariff").exclude(source__in=NOT_REPRICED).filter(tariff__isnull=False):
        new = q(item.tariff.rate_for(category))
        if new != item.unit_price:
            out.append((item, item.unit_price, new))
    return out


@transaction.atomic
def change_category(bill, category, user, *, apply=True):
    if category not in CATEGORIES:
        raise ValidationError({"category": f"One of {', '.join(CATEGORIES)}."})
    if bill.is_interim or bill.status in (Bill.Status.PAID, Bill.Status.CANCELLED):
        raise ValidationError({"detail": "A paid or cancelled bill isn't re-priced; issue a credit note instead."})
    ensure_open(bill, "re-price it")
    changes = reprice_preview(bill, category)
    before = bill.net_amount
    if apply:
        for item, _old, new in changes:
            item.unit_price = new
            item.save()  # recomputes total and tax
        bill.patient_category = category
        bill.save(update_fields=["patient_category"])
        recalculate_bill(bill)
    return {
        "category": category, "applied": apply, "net_before": before, "net_after": bill.net_amount if apply else before + sum(
            ((new - old) * i.quantity * (1 + Decimal(i.gst_rate or 0) / 100) for i, old, new in changes), ZERO).quantize(CENT),
        "lines": [{"item": i.pk, "description": i.description, "quantity": i.quantity, "old_rate": old, "new_rate": new} for i, old, new in changes],
    }


# --- credit notes -----------------------------------------------------------------

def _credited(item):
    """Already credited against this line (positive number)."""
    return -sum((i.total_price for i in item.bill.items.filter(source="credit_note", source_ref__endswith=f":{item.pk}")), ZERO)


def _creditable_lines(bill):
    lines = [i for i in bill.items.all() if i.source != "credit_note" and i.total_price > 0]
    return [(i, i.total_price - _credited(i)) for i in lines]


def _allocate(bill, amount, items):
    """[(item, taxable_credit)]"""
    lines = _creditable_lines(bill)
    room = {i.pk: r for i, r in lines}
    if items:
        out = []
        for row in items:
            item = next((i for i, _ in lines if i.pk == int(row.get("item", 0) or 0)), None)
            if item is None:
                raise ValidationError({"items": f"Line {row.get('item')} isn't a creditable line on this bill."})
            amt = q(row.get("amount") or 0)
            if amt <= 0 or amt > room[item.pk]:
                raise ValidationError({"items": f"{item.description}: credit between 0.01 and {room[item.pk]}."})
            out.append((item, amt))
        return out
    amount = q(amount or 0)
    total_room = sum((r for _, r in lines), ZERO)
    if amount <= 0 or amount > total_room:
        raise ValidationError({"amount": f"Credit between 0.01 and {total_room} (before GST)."})
    out, left = [], amount
    for n, (item, r) in enumerate(lines):
        share = left if n == len(lines) - 1 else min(q(amount * r / total_room), left)
        if share > 0:
            out.append((item, share))
            left -= share
    return out


def _number(bill):
    yy = f"{timezone.localdate():%y}"
    n = BillCreditNote.objects.filter(hospital_id=bill.hospital_id, note_number__startswith=f"CN{yy}").count() + 1
    return f"CN{yy}{n:06d}"


@transaction.atomic
def credit_note(bill, user, *, reason, amount=None, items=None, settlement=BillCreditNote.Settlement.REDUCE_DUE, refund_method="", refund_reference=""):
    bill = Bill.objects.select_for_update().get(pk=bill.pk)
    if bill.is_interim or bill.status in (Bill.Status.DRAFT, Bill.Status.CANCELLED):
        raise ValidationError({"detail": "Credit notes are for finalised bills (not drafts, interim or cancelled bills)."})
    ensure_open(bill, "issue a credit note")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Give a reason for the credit."})
    if settlement not in BillCreditNote.Settlement.values:
        raise ValidationError({"settlement": "reduce_due | advance | refund"})
    allocation = _allocate(bill, amount, items or [])
    tax = sum((q(a * Decimal(i.gst_rate or 0) / 100) for i, a in allocation), ZERO)
    taxable = sum((a for _, a in allocation), ZERO)

    paid = sum((p.amount for p in bill.payments.all()), ZERO)
    new_net = bill.net_amount - taxable - tax
    excess = q(max(paid - max(new_net, ZERO), ZERO))
    if excess and settlement == BillCreditNote.Settlement.REDUCE_DUE:
        raise ValidationError({"settlement": f"₹{excess} has already been paid beyond the new total; refund it or keep it as an advance."})
    if settlement == BillCreditNote.Settlement.REFUND and refund_method not in REFUND_METHODS:
        raise ValidationError({"refund_method": f"One of {', '.join(REFUND_METHODS)}."})

    note = BillCreditNote.objects.create(
        hospital_id=bill.hospital_id, bill=bill, note_number=_number(bill), reason=reason[:255], taxable_amount=taxable, tax_amount=tax,
        total_amount=taxable + tax, settlement=settlement, refund_amount=excess,
        refund_method=refund_method if settlement == BillCreditNote.Settlement.REFUND else ("advance" if excess else ""),
        refund_reference=refund_reference[:100], created_by=user,
        allocation=[{"item": i.pk, "description": i.description, "amount": str(a), "tax": str(q(a * Decimal(i.gst_rate or 0) / 100))} for i, a in allocation],
    )
    for item, amt in allocation:
        BillItem.objects.create(bill=bill, description=f"Credit note {note.note_number}: {item.description}"[:255], quantity=1, unit_price=-amt, total_price=0,
                                tariff=item.tariff, hsn_sac=item.hsn_sac, gst_rate=item.gst_rate, service_date=timezone.localdate(),
                                source="credit_note", source_ref=f"cn:{note.pk}:{item.pk}")
    recalculate_bill(bill)
    if excess:
        method = "advance" if settlement == BillCreditNote.Settlement.ADVANCE else refund_method
        Payment.objects.create(hospital_id=bill.hospital_id, bill=bill, amount=-excess, payment_method=method,
                               transaction_id=f"{note.note_number} {refund_reference}".strip()[:100])
        if settlement == BillCreditNote.Settlement.ADVANCE:
            PatientAdvance.objects.create(hospital_id=bill.hospital_id, patient=bill.patient, kind=PatientAdvance.Kind.DEPOSIT, amount=excess,
                                          payment_method="credit_note", reference=note.note_number, recorded_by=user)
        recalculate_bill(bill)
    return note
