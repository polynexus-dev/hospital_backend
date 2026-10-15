"""Doctor payout engine: which bill lines a doctor has earned in a period,
under which rule, for how much — and the statements that pay them."""
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .models_payout import DoctorPayout, DoctorPayoutDeduction, DoctorPayoutLine, DoctorPayoutRule, PayoutDeductionHead

CENT = Decimal("0.01")
# System lines that are never a doctor's professional service.
EXCLUDED_SOURCES = ("bed_charge", "interim", "scheme_package", "scheme_adjustment")


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


def _gross(bill):
    return sum((Decimal(i.total_price) for i in bill.items.all() if i.total_price > 0 and i.source != "scheme_package"), Decimal("0"))


def net_base(item):
    """The line's share of the bill after discount — and after any scheme
    package adjustment, which is a reduction just like a discount — before GST."""
    bill = item.bill
    base = Decimal(item.total_price)
    lines = list(bill.items.all())
    gross = _gross(bill)
    reduction = Decimal(bill.discount_amount or 0) - sum((Decimal(i.total_price) for i in lines if i.source == "scheme_adjustment"), Decimal("0"))
    reduction -= sum((Decimal(i.total_price) for i in lines if i.source == "scheme_package"), Decimal("0"))  # package revenue offsets it
    if reduction > 0 and gross:
        base -= reduction * base / gross
    return _q(max(base, Decimal("0")))


def bill_medicine_cost(bill):
    """Purchase cost of medicines dispensed to the patient during the bill's
    admission, less what was returned to stock. OPD bills: 0 (pharmacy
    counter sales are settled separately)."""
    from apps.pharmacy.models import DispenseRecord, MedicineReturn

    adm = bill.admission
    if adm is None:
        return Decimal("0")
    window = {"patient_id": bill.patient_id, "hospital_id": bill.hospital_id}
    span = (adm.admitted_at, adm.discharged_at or timezone.now())
    issued = sum((Decimal(d.quantity) * Decimal(d.batch.purchase_price) for d in
                  DispenseRecord.objects.filter(**window, dispensed_at__range=span).select_related("batch")), Decimal("0"))
    returned = sum((Decimal(r.quantity) * Decimal(r.batch.purchase_price) for r in
                    MedicineReturn.objects.filter(**window, restocked=True, created_at__range=span).select_related("batch")), Decimal("0"))
    return _q(max(issued - returned, Decimal("0")))


PHARMACY_SOURCES = ("pharmacy", "pharmacy_return")


def medicine_share(item, cache):
    """This line's part of the bill's medicine cost, split by line value
    over the bill's services — the medicine lines themselves (apps.pharmacy.
    billing) don't take a share of their own cost."""
    bill = item.bill
    if bill.pk not in cache:
        services = sum((Decimal(i.total_price) for i in bill.items.all() if i.total_price > 0 and i.source not in ("scheme_package", *PHARMACY_SOURCES)), Decimal("0"))
        cache[bill.pk] = (bill_medicine_cost(bill), services)
    cost, gross = cache[bill.pk]
    if not cost or not gross or item.total_price <= 0:
        return Decimal("0")
    return _q(cost * Decimal(item.total_price) / gross)


def payout_for(item, rule, base=None):
    if rule.basis == DoctorPayoutRule.Basis.FIXED:
        return _q(Decimal(rule.value) * item.quantity)
    return _q((net_base(item) if base is None else base) * Decimal(rule.value) / 100)


def eligible_lines(hospital_id, start, end, doctor=None):
    """[(item, rule, earned_date, base, payout, medicine_cost)] not yet on a
    live statement. `base` is already net of medicine cost when the rule
    deducts it."""
    from apps.billing.models import Bill, BillItem

    items = (
        BillItem.objects.filter(bill__hospital_id=hospital_id, doctor__isnull=False, payout_line__isnull=True, bill__is_interim=False)
        .exclude(source__in=EXCLUDED_SOURCES)
        .exclude(bill__status__in=[Bill.Status.DRAFT, Bill.Status.CANCELLED])
        .select_related("bill__patient", "bill__admission", "doctor", "tariff")
        .annotate(last_payment=Max("bill__payments__paid_at"))
    )
    if doctor:
        items = items.filter(doctor=doctor)
    rules = list(DoctorPayoutRule.objects.filter(hospital_id=hospital_id, is_active=True))
    out, med_cache = [], {}
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
            base, med = net_base(item), Decimal("0")
            if rule.deduct_medicine_cost and rule.basis == DoctorPayoutRule.Basis.PERCENT:
                med = min(medicine_share(item, med_cache), base)
                base -= med
            out.append((item, rule, earned, base, payout_for(item, rule, base), med))
    return out


def _rule_label(rule):
    value = f"{rule.value:g}%" if rule.basis == DoctorPayoutRule.Basis.PERCENT else f"₹{rule.value:g}/unit"
    net = ", net of medicine cost" if rule.deduct_medicine_cost and rule.basis == DoctorPayoutRule.Basis.PERCENT else ""
    return f"{rule.name} ({value}{net}, {rule.get_earned_on_display().lower()})"


@transaction.atomic
def generate_statements(hospital_id, start, end, *, doctor=None, tds_percent=Decimal("10")):
    """Creates a draft statement per doctor with earnings in the period.
    Re-running first dissolves that period's existing drafts, so a draft is
    always current; approved and paid statements are never touched."""
    drafts = DoctorPayout.objects.filter(hospital_id=hospital_id, period_start=start, period_end=end, status=DoctorPayout.Status.DRAFT)
    if doctor:
        drafts = drafts.filter(doctor=doctor)
    DoctorPayoutLine.objects.filter(payout__in=drafts).delete()
    DoctorPayoutDeduction.objects.filter(payout__in=drafts).delete()
    drafts.delete()
    auto_heads = list(PayoutDeductionHead.objects.filter(hospital_id=hospital_id, is_active=True, auto_apply=True, default_amount__gt=0))

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
                quantity=item.quantity, base_amount=base, medicine_cost=med, payout_amount=amount,
            )
            for item, rule, earned, base, amount, med in rows
        ])
        DoctorPayoutDeduction.objects.bulk_create([
            DoctorPayoutDeduction(payout=payout, head=h, amount=h.default_amount, remarks="Applied automatically") for h in auto_heads
        ])
        refresh_totals(payout)
        statements.append(payout)
    return statements


def refresh_totals(payout):
    lines = list(payout.lines.all())
    payout.base_amount = sum((ln.base_amount for ln in lines), Decimal("0"))
    payout.gross_payout = sum((ln.payout_amount for ln in lines), Decimal("0"))
    payout.tds_amount = _q(payout.gross_payout * Decimal(payout.tds_percent) / 100)
    payout.deductions_total = sum((d.amount for d in payout.deductions.all()), Decimal("0"))
    payout.net_payable = payout.gross_payout - payout.tds_amount - payout.deductions_total
    payout.save(update_fields=["base_amount", "gross_payout", "tds_amount", "deductions_total", "net_payable"])


def post_payout_journal(payout):
    """Dr doctor professional fees; Cr bank/cash (net), TDS payable, and
    each deduction head's ledger (recoveries)."""
    from . import accounting

    bank = "1000" if payout.payment_mode == "cash" else "1010"
    recoveries = defaultdict(Decimal)
    for d in payout.deductions.select_related("head"):
        recoveries[d.head.ledger_code] += d.amount
    lines = [("5100", payout.gross_payout, 0), (bank, 0, payout.net_payable), ("2110", 0, payout.tds_amount)]
    lines += [(code, 0, amt) for code, amt in sorted(recoveries.items()) if amt]
    return accounting.post(
        payout.hospital_id, voucher_type="payment", voucher_number=f"DP{payout.pk}", source_type="doctor_payout", source_id=payout.pk,
        lines=lines,
        narration=f"Professional fees {payout.period_start}–{payout.period_end} ({payout.payment_reference})",
        party=f"Dr. {payout.doctor.name}", entry_date=payout.paid_on,
    )
