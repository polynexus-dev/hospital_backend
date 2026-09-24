"""Doctor payout engine: which bill lines a doctor has earned in a period,
under which rule, for how much — and the statements that pay them."""
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models_payout import DoctorPayout, DoctorPayoutLine, DoctorPayoutRule

CENT = Decimal("0.01")
# System lines that are never a doctor's professional service.
EXCLUDED_SOURCES = ("bed_charge", "interim")


def _q(x):
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def _specificity(rule):
    return (
        (16 if rule.doctor_id else 0) + (8 if rule.tariff_id else 0) + (4 if rule.service_department else 0)
        + (2 if rule.patient_category else 0) + (1 if rule.doctor_department_id else 0),
        rule.priority,
        -rule.pk,
    )


def matching_rule(item, rules, on_date):
    doctor, bill, tariff = item.doctor, item.bill, item.tariff
    candidates = [
        r for r in rules
        if (not r.doctor_id or r.doctor_id == doctor.pk)
        and (not r.doctor_department_id or r.doctor_department_id == doctor.department_id)
        and (not r.tariff_id or r.tariff_id == item.tariff_id)
        and (not r.service_department or (tariff is not None and tariff.department.lower() == r.service_department.lower()))
        and (not r.patient_category or r.patient_category == bill.patient_category)
        and (not r.effective_from or r.effective_from <= on_date)
        and (not r.effective_to or on_date <= r.effective_to)
    ]
    return max(candidates, key=_specificity) if candidates else None


def net_base(item):
    """The line's share of the bill after discount, before GST."""
    bill = item.bill
    base = Decimal(item.total_price)
    discount = Decimal(bill.discount_amount or 0)
    if discount and bill.total_amount:
        base -= discount * base / Decimal(bill.total_amount)
    return _q(max(base, Decimal("0")))


def payout_for(item, rule):
    if rule.basis == DoctorPayoutRule.Basis.FIXED:
        return _q(Decimal(rule.value) * item.quantity)
    return _q(net_base(item) * Decimal(rule.value) / 100)


def eligible_lines(hospital_id, start, end, doctor=None):
    """[(item, rule, earned_date, base, payout)] not yet on a live statement."""
    from apps.billing.models import Bill, BillItem

    items = (
        BillItem.objects.filter(bill__hospital_id=hospital_id, doctor__isnull=False, payout_line__isnull=True, bill__is_interim=False)
        .exclude(source__in=EXCLUDED_SOURCES)
        .exclude(bill__status__in=[Bill.Status.DRAFT, Bill.Status.CANCELLED])
        .select_related("bill__patient", "doctor", "tariff")
        .annotate(last_payment=Max("bill__payments__paid_at"))
    )
    if doctor:
        items = items.filter(doctor=doctor)
    rules = list(DoctorPayoutRule.objects.filter(hospital_id=hospital_id, is_active=True))
    out = []
    for item in items:
        billed_on = item.service_date or timezone.localdate(item.bill.created_at)
        rule = matching_rule(item, rules, billed_on)
        if rule is None:
            continue
        if rule.earned_on == DoctorPayoutRule.EarnedOn.COLLECTED:
            if item.bill.status != Bill.Status.PAID or item.last_payment is None:
                continue
            earned = timezone.localdate(item.last_payment)
        else:
            earned = billed_on
        if start <= earned <= end:
            out.append((item, rule, earned, net_base(item), payout_for(item, rule)))
    return out


def _rule_label(rule):
    value = f"{rule.value:g}%" if rule.basis == DoctorPayoutRule.Basis.PERCENT else f"₹{rule.value:g}/unit"
    return f"{rule.name} ({value}, {rule.get_earned_on_display().lower()})"


@transaction.atomic
def generate_statements(hospital_id, start, end, *, doctor=None, tds_percent=Decimal("10")):
    """Creates a draft statement per doctor with earnings in the period.
    Re-running first dissolves that period's existing drafts, so a draft is
    always current; approved and paid statements are never touched."""
    drafts = DoctorPayout.objects.filter(hospital_id=hospital_id, period_start=start, period_end=end, status=DoctorPayout.Status.DRAFT)
    if doctor:
        drafts = drafts.filter(doctor=doctor)
    DoctorPayoutLine.objects.filter(payout__in=drafts).delete()
    drafts.delete()

    by_doctor = defaultdict(list)
    for row in eligible_lines(hospital_id, start, end, doctor):
        by_doctor[row[0].doctor].append(row)

    statements = []
    for doc, rows in sorted(by_doctor.items(), key=lambda kv: kv[0].name):
        payout = DoctorPayout.objects.create(hospital_id=hospital_id, doctor=doc, period_start=start, period_end=end, tds_percent=tds_percent)
        DoctorPayoutLine.objects.bulk_create([
            DoctorPayoutLine(
                payout=payout, bill_item=item, rule=rule, rule_label=_rule_label(rule), bill_number=item.bill.bill_number,
                patient_name=item.bill.patient.full_name, description=item.description, service_date=earned,
                quantity=item.quantity, base_amount=base, payout_amount=amount,
            )
            for item, rule, earned, base, amount in rows
        ])
        refresh_totals(payout)
        statements.append(payout)
    return statements


def refresh_totals(payout):
    lines = list(payout.lines.all())
    payout.base_amount = sum((ln.base_amount for ln in lines), Decimal("0"))
    payout.gross_payout = sum((ln.payout_amount for ln in lines), Decimal("0"))
    payout.tds_amount = _q(payout.gross_payout * Decimal(payout.tds_percent) / 100)
    payout.net_payable = payout.gross_payout - payout.tds_amount
    payout.save(update_fields=["base_amount", "gross_payout", "tds_amount", "net_payable"])


def post_payout_journal(payout):
    """Dr doctor professional fees; Cr bank/cash (net) and TDS payable."""
    from . import accounting

    bank = "1000" if payout.payment_mode == "cash" else "1010"
    return accounting.post(
        payout.hospital_id, voucher_type="payment", voucher_number=f"DP{payout.pk}", source_type="doctor_payout", source_id=payout.pk,
        lines=[("5100", payout.gross_payout, 0), (bank, 0, payout.net_payable), ("2110", 0, payout.tds_amount)],
        narration=f"Professional fees {payout.period_start}–{payout.period_end} ({payout.payment_reference})",
        party=f"Dr. {payout.doctor.name}", entry_date=payout.paid_on,
    )
